import json
import pytest

from src.agent.result import validate_result
from src.agent.providers import FrozenSnapshot
from src.agent.tools import ToolSession
from src.agent.errors import AgentError
from tests.agent.test_claim_selection import movement_snapshot


def session():
    data = movement_snapshot().export()
    data['observations'][0]['score'] = 1.0
    result = ToolSession(FrozenSnapshot(data))
    result.execute('get_incident', json.dumps({'incident_id': result.snapshot.incident_id}))
    return result


def answer():
    return {'reference_facts': [{'source': 'model_observation', 'id': 'linked-mlp', 'field': field}
                               for field in ('status', 'score', 'threshold')],
            'hypotheses': [], 'recommendations': ['Проверьте объект на карте.']}


def test_integral_score_is_selected_from_executed_tool_without_numeric_conversion():
    tools = session()
    before = tools.snapshot.export()
    result = validate_result(json.dumps(answer()), tools, 'fixture')
    score = next(row['value'] for row in result['facts'] if row['field'] == 'score')
    assert type(score) is float and score == 1.0
    assert result['technical']['model_references'] == answer()['reference_facts']
    assert tools.snapshot.export() == before
    assert result['evidence_event_ids'] == ['demo-position-0010']


@pytest.mark.parametrize('invalid', ['foreign', 'duplicate', 'number', 'unread', 'missing_event'])
def test_bad_reference_or_proof_is_still_rejected(invalid):
    tools, body = session(), answer()
    if invalid == 'foreign': body['reference_facts'][0]['id'] = 'other-observation'
    if invalid == 'duplicate': body['reference_facts'][0] = body['reference_facts'][1]
    if invalid == 'number': body['reference_facts'][1]['value'] = 1
    if invalid == 'unread': tools.records['model_observation'].clear()
    if invalid == 'missing_event': tools.records['event'].clear()
    with pytest.raises(AgentError): validate_result(json.dumps(body), tools, 'fixture')


def test_legacy_numeric_claim_is_not_repaired_or_relaxed():
    tools, body = session(), answer()
    refs = body.pop('reference_facts')
    body['facts'] = [{**ref, 'value': tools.records['model_observation'][ref['id']][ref['field']]}
                     for ref in refs]
    body['facts'][1]['value'] = 1
    with pytest.raises(AgentError, match='Fact ID/field/value'):
        validate_result(json.dumps(body), tools, 'fixture')
