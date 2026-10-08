"""Readable output must retain the strict executed-tool evidence boundary."""
import json
from pathlib import Path

import pytest

from src.agent.errors import AgentError
from src.agent.presentation import present
from src.agent.providers import FrozenSnapshot
from src.agent.result import validate_result
from src.agent.tools import ToolSession


def data():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/agent_v2.json').read_text(encoding='utf-8'))
    value = next(row for row in fixture['snapshots'] if row['incident']['incident_id'] == 'INC-ZONE-1')
    value['display_context'] = {
        'assets': {'V1': 'Погрузчик 1'}, 'zones': {'ZONE-W1': 'Закрытая погрузочная зона'},
        'coordinate_system': {'units': 'plan', 'y_direction': 'down'}, 'zone_exit_confirm_samples': 2}
    value['incident'].update(condition_state='restored', condition_active=False,
                             rule_version='rules-v2.2-safety', details={'exit_samples': 2},
                             response_plan={'contact': 'Диспетчер участка и служба безопасности'})
    return value


def session(value):
    result = ToolSession(FrozenSnapshot(value))
    result.execute('get_incident', json.dumps({'incident_id': result.snapshot.incident_id}))
    return result


def result(value):
    tools = session(value)
    event = next(iter(tools.records['event'].values()))
    reply = {'facts': [{'source': 'event', 'id': event['event_id'], 'field': 'payload.y',
                        'value': event['payload']['y']}], 'hypotheses': [],
             'recommendations': ['Непроверенное требование модели: объявить пожар.']}
    return validate_result(json.dumps(reply), tools, 'explicit-test-double')


def test_readable_names_and_units_without_raw_fields_or_unverified_recommendations():
    output = result(data())
    main = json.dumps(output['presentation'], ensure_ascii=False)
    assert 'Погрузчик 1' in main and 'Закрытая погрузочная зона' in main
    assert 'условных единицах' in main and 'Перевод в метры не задан' in main
    assert 'payload.y' not in main and 'demo-position-' not in main
    assert 'пожар' not in main
    assert 'пожар' in output['technical']['model_answer']['recommendations'][0]
    assert output['technical']['verified_facts'][0]['field'] == 'payload.y'
    assert output['technical']['snapshot']['events'] == data()['events']
    assert output['presentation']['state']['confirmed_exit'] is True
    assert output['presentation']['as_of'] == data()['as_of']


@pytest.mark.parametrize('changes', [
    {'details': {'exit_samples': 1}}, {'details': {'exit_samples': True}},
    {'details': None}, {'rule_version': 'unknown'}, {'condition_active': True},
    {'condition_state': 'unknown'}, {'condition_state': 'active'},
])
def test_exit_is_never_inferred_from_restored_state_alone(changes):
    value = data()
    value['incident'].update(changes)
    output = result(value)['presentation']
    assert output['state']['confirmed_exit'] is False
    assert 'Выход из зоны подтверждён' not in output['state']['text']


def test_identity_and_unknown_fields_do_not_create_empty_observations():
    tools = session(data())
    facts = [{'source': 'event', 'id': 'demo-position-0010', 'field': field, 'value': value}
             for field, value in [('payload.asset_id', 'V1'), ('type', 'position'),
                                  ('event_time', '2026-10-07T09:00:10Z'), ('payload.unknown', 123)]]
    assert present(tools, facts)['observations'] == []


def test_mlp_numbers_are_readable_and_do_not_use_incident_details_as_evidence():
    value = data()
    value['incident'].update(type='model_anomaly', details={'observation_id': 'OBS-1', 'score': 999, 'threshold': 999})
    tools = session(value)
    facts = [{'source': 'model_observation', 'id': 'OBS-1', 'field': field, 'value': val}
             for field, val in [('status', 'anomaly'), ('score', 0.9998759021886874), ('threshold', 0.15122588236235446)]]
    text = ' '.join(present(tools, facts)['observations'])
    assert '≈ 0,999876' in text and '≈ 0,151226' in text
    assert 'не является вероятностью ДТП' in text and 'Числа округлены' in text
    assert '999.' not in text
    missing = present(tools, [])['observations']
    assert not any(line.startswith('Оценка необычности движения:') for line in missing)


def test_site_area_is_not_described_as_inside_a_building():
    value = data()
    value['incident'].update(type='model_anomaly', zone_id=None, site_area_id='canteen')
    value['display_context']['site_areas'] = {'canteen': 'Столовая'}
    output = present(session(value), [])
    assert output['place_kind'] == 'site_area'
    assert 'Участок карты: «Столовая» (по сохранённым данным)' in output['description']
    assert 'внутри' not in output['description']
    assert 'Выход из зоны подтверждён' not in output['state']['text']


def test_missing_names_units_state_and_contact_are_explicit():
    value = data()
    value.pop('display_context')
    value['incident'].pop('condition_state')
    value['incident']['response_plan'] = None
    main = json.dumps(result(value)['presentation'], ensure_ascii=False)
    for text in ('Объект не определён', 'Место не определено', 'Состояние объекта не указано',
                 'физический смысл не указаны', 'Контакт для реакции не указан'):
        assert text in main
    assert 'V1' not in main and 'ZONE-W1' not in main


def test_captured_names_do_not_follow_later_registry_changes():
    value = data()
    tools = session(value)
    value['display_context']['assets']['V1'] = 'Переименован после среза'
    assert present(tools, [])['entity'] == 'Погрузчик 1'
    assert FrozenSnapshot(value).snapshot_id != tools.snapshot.snapshot_id


@pytest.mark.parametrize('required', [None, True, 0, 3])
def test_exit_needs_the_captured_rule_confirmation_threshold(required):
    value = data()
    value['display_context']['zone_exit_confirm_samples'] = required
    assert result(value)['presentation']['state']['confirmed_exit'] is False


def test_false_fact_still_rejected_before_presentation():
    tools = session(data())
    reply = {'facts': [{'source': 'event', 'id': 'demo-position-0010', 'field': 'payload.y', 'value': 999}],
             'hypotheses': [], 'recommendations': ['Проверить.']}
    with pytest.raises(AgentError) as caught:
        validate_result(json.dumps(reply), tools, 'explicit-test-double')
    assert caught.value.code == 'invalid_evidence'


def test_missing_coordinate_is_not_reported_as_measured_position():
    value = data()
    for event in value['events']:
        event['payload']['y'] = None
    output = result(value)['presentation']
    text = ' '.join(output['observations'])
    assert 'Значение координаты отсутствует' in text
    assert 'передал вертикальную координату' not in text


@pytest.mark.parametrize('score,threshold,status,exceeds', [(0.8, 0.5, 'anomaly', True),
    (0.4, 0.5, 'anomaly', False), (0.8, 0.5, 'normal', False), (None, 0.5, 'anomaly', False)])
def test_model_exceedance_is_derived_only_from_linked_verified_fields(score, threshold, status, exceeds):
    value = data()
    value['incident'].update(type='model_anomaly', details={'observation_id': 'OBS-1'})
    tools = session(value)
    facts = [{'source': 'model_observation', 'id': 'OBS-1', 'field': field, 'value': val}
             for field, val in [('status', status), ('score', score), ('threshold', threshold)]]
    output = present(tools, facts)
    assert ('достигла или превысила' in ' '.join(output['observations'])) is exceeds
    assert 'Причина движения моделью не подтверждена' in ' '.join(output['observations'])
    assert output['state']['confirmed_exit'] is False
