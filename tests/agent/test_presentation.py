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


def test_readable_names_without_numbers_raw_fields_or_unverified_recommendations():
    output = result(data())
    main = json.dumps(output['presentation'], ensure_ascii=False)
    assert 'Погрузчик 1' in main and 'Закрытая погрузочная зона' in main
    assert 'координат' not in main and 'порог' not in main and 'оценка' not in main
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


def test_mlp_conclusion_uses_verified_fields_and_hides_numbers():
    value = data()
    value['incident'].update(type='model_anomaly', condition_state='active', condition_active=True,
                             details={'observation_id': 'OBS-1', 'score': 999, 'threshold': 999})
    tools = session(value)
    facts = [{'source': 'model_observation', 'id': 'OBS-1', 'field': field, 'value': val}
             for field, val in [('status', 'anomaly'), ('score', 0.9998759021886874), ('threshold', 0.15122588236235446)]]
    output = present(tools, facts)
    assert output['title'] == 'Обнаружено необычное движение'
    assert output['unknown'] == 'Причина движения неизвестна. Наличие аварии не установлено.'
    text = json.dumps(output, ensure_ascii=False)
    assert '0,999876' not in text and '0.151225' not in text and '999' not in text
    assert present(tools, [])['title'] == 'Данных недостаточно для вывода'


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
                 'Уточните ответственного'):
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
    text = json.dumps(output, ensure_ascii=False)
    assert 'координат' not in text
    assert result(value)['technical']['verified_facts'][0]['value'] is None


@pytest.mark.parametrize('score,threshold,status,exceeds', [(0.8, 0.5, 'anomaly', True),
    (0.4, 0.5, 'anomaly', False), (0.8, 0.5, 'normal', False), (None, 0.5, 'anomaly', False)])
def test_model_exceedance_is_derived_only_from_linked_verified_fields(score, threshold, status, exceeds):
    value = data()
    value['incident'].update(type='model_anomaly', condition_state='active', condition_active=True,
                             details={'observation_id': 'OBS-1'})
    tools = session(value)
    facts = [{'source': 'model_observation', 'id': 'OBS-1', 'field': field, 'value': val}
             for field, val in [('status', status), ('score', score), ('threshold', threshold)]]
    output = present(tools, facts)
    assert (output['title'] == 'Обнаружено необычное движение') is exceeds
    assert 'порог' not in json.dumps(output, ensure_ascii=False)
    assert output['state']['confirmed_exit'] is False


@pytest.mark.parametrize('content', [
    'Погрузчик занят погрузкой.', 'Погрузчик вышел из зоны.',
    'Высокая вероятность ДТП: 99%.', 'Водитель уснул.',
])
def test_valid_json_does_not_authorize_unverified_model_explanation(content):
    tools = session(data())
    reply = {'facts': [{'source': 'event', 'id': 'demo-position-0010', 'field': 'payload.x', 'value': 20.0}],
             'hypotheses': [{'text': content, 'confidence': 'high', 'limitations': ['Не подтверждено.']}],
             'recommendations': [content]}
    output = validate_result(json.dumps(reply), tools, 'explicit-test-double')
    assert content not in json.dumps(output['presentation'], ensure_ascii=False)
    review = output['technical']['semantic_check']['items']
    assert len(review) == 2 and not any(row['admitted'] for row in review)
    assert all(row['text'] == content for row in review)


def test_semantic_check_allows_only_exact_supported_action_not_a_dangerous_suffix():
    tools = session(data())
    action = present(tools, [])['recommendations'][0]
    reply = {'facts': [{'source': 'event', 'id': 'demo-position-0010', 'field': 'payload.x', 'value': 20.0}],
             'hypotheses': [], 'recommendations': [action, action + ' Объявите аварию.']}
    output = validate_result(json.dumps(reply), tools, 'explicit-test-double')
    assert [row['admitted'] for row in output['technical']['semantic_check']['items']] == [True, False]
    assert 'Объявите аварию' not in json.dumps(output['presentation'], ensure_ascii=False)


def test_zone_unknown_rule_or_missing_tool_evidence_cannot_confirm_violation():
    value = data()
    value['incident'].update(condition_state='active', condition_active=True)
    tools = ToolSession(FrozenSnapshot(value))
    assert present(tools, [])['title'] == 'Данных недостаточно для вывода'
    value['incident']['rule_version'] = 'unrecognized-rule'
    assert present(session(value), [])['title'] == 'Данных недостаточно для вывода'


def test_main_strings_have_no_jargon_or_decimal_evaluations():
    import re
    output = result(data())['presentation']
    visible = ' '.join([output[key] for key in ('title', 'description', 'established', 'attention', 'unknown')]
                       + [output['state']['text']] + output['observations'] + output['recommendations'])
    assert not re.search(r'MLP|Qwen|JSON|payload|скор|порог|оценк|модель|алгоритм|\d+[.,]\d+|≈', visible, re.I)
    for key in ('established', 'attention', 'unknown'):
        assert output[key]


@pytest.mark.parametrize('windows,required,confirmed', [(1, 2, False), (2, 2, True),
                                                      (True, 2, False), (2, 3, False)])
def test_movement_restoration_requires_the_saved_rule_confirmation(windows, required, confirmed):
    value = data()
    value['incident'].update(type='model_anomaly', details={'observation_id': 'OBS-1', 'normal_windows': windows})
    value['display_context']['model_normal_windows'] = required
    facts = [{'source': 'model_observation', 'id': 'OBS-1', 'field': field, 'value': val}
             for field, val in [('status', 'anomaly'), ('score', 0.8), ('threshold', 0.5)]]
    output = present(session(value), facts)
    assert (output['title'] == 'Необычное движение перестало наблюдаться') is confirmed
    assert ('последующие проверки' in output['state']['text'].lower()) is confirmed
