import asyncio
import json

import pytest

from src.agent.config import AgentConfig
from src.agent.errors import AgentError
from src.agent.loop import run_analysis, verified_claim_choices, model_tool_result
from src.agent.providers import FrozenSnapshot
from src.agent.result import validate_result
from src.agent.tools import ToolSession
from tests.agent.test_tools_and_result import snapshot
from tests.agent.test_runtime import tool_call


def movement_snapshot():
    data = snapshot().export()
    observation = {'observation_id': 'linked-mlp', 'asset_id': 'V1',
                   'status': 'anomaly', 'score': .99997321, 'threshold': .9057877839749207,
                   'evidence_event_ids': ['demo-position-0010']}
    data['incident'].update(type='model_anomaly', details={'observation_id': 'linked-mlp'})
    data['observations'] = [observation]
    return FrozenSnapshot(data)


def reply(facts):
    return {'role': 'assistant', 'content': json.dumps({
        ('reference_facts' if facts and all('value' not in row for row in facts) else 'facts'): facts,
        'hypotheses': [], 'recommendations': ['Проверьте объект на карте.']})}


def test_model_prompt_selection_is_explicit_bounded_and_does_not_change_tool_data():
    events = [{'event_id': f'e{i}', 'payload': {'asset_id': 'V1' if i % 2 else 'V2',
                                              'x': i, 'y': 3}} for i in range(100)]
    ids = [row['event_id'] for row in events]
    result = {'incident': {'evidence_event_ids': ids}, 'evidence': events,
              'observations': [{'observation_id': 'o', 'evidence_event_ids': ids,
                                'score': .99, 'threshold': .9, 'status': 'anomaly'}]}
    before = json.dumps(result)
    compact, visible = model_tool_result(result)
    assert json.dumps(result) == before
    assert len(visible) <= 14 and {'e0', 'e1', 'e99'} <= visible
    assert compact['model_input_selection']['partial']
    assert compact['model_input_selection']['available_event_count'] == 100
    assert set(compact['incident']['evidence_event_ids']) <= visible
    observation = compact['observations'][0]
    assert observation['evidence_partial'] and observation['verified_evidence_count'] == 100
    assert observation['score'] == .99


def test_route_candidates_exclude_unbacked_observations_but_keep_measured_events():
    data = snapshot().export()
    data['incident']['type'] = 'route_deviation'
    data['observations'] = [{'observation_id': 'empty-window', 'asset_id': 'V1',
                             'status': 'insufficient_data', 'score': None,
                             'threshold': .9, 'evidence_event_ids': []}]
    session = ToolSession(FrozenSnapshot(data))
    session.execute('get_incident', json.dumps({'incident_id': session.snapshot.incident_id}))
    choices = verified_claim_choices(session)
    assert choices and any(row['source'] == 'event' for row in choices)
    assert not any(row['source'] == 'model_observation' for row in choices)
    # The verifier still rejects the very observation that selection excluded.
    with pytest.raises(AgentError, match='Observation evidence'):
        validate_result(reply([{'source': 'model_observation', 'id': 'empty-window',
                               'field': 'status', 'value': 'insufficient_data'}])['content'], session, 'fixture')


@pytest.mark.parametrize('recover', [True, False])
def test_mlp_requires_complete_tuple_and_retries_without_repairing_claims(recover):
    class Client:
        calls = 0
        deadlines = []

        async def chat(self, messages, tools, deadline, response_schema=None):
            self.calls += 1
            self.deadlines.append(deadline)
            if self.calls == 1:
                return {'role': 'assistant', 'content': '', 'tool_calls': [tool_call()]}
            claims = response_schema['$defs']['FactClaim']['enum']
            assert response_schema['properties']['reference_facts']['enum'] == [claims]
            assert [row['field'] for row in claims] == ['status', 'score', 'threshold']
            # Simulate a local provider ignoring the schema: incomplete evidence
            # must never be published or filled in by the program.
            return reply(claims if recover and self.calls == 3 else [claims[1]] * 3)

    client = Client()
    async def run():
        if recover:
            result = await run_analysis(client, movement_snapshot(), AgentConfig(model='fixture'))
            assert [row['field'] for row in result['facts']] == ['status', 'score', 'threshold']
        else:
            with pytest.raises(AgentError, match='status, score'):
                await run_analysis(client, movement_snapshot(), AgentConfig(model='fixture'))
    asyncio.run(run())
    assert client.calls == 3
    assert len(set(client.deadlines)) == 1
