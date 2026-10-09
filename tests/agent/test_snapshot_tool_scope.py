import asyncio
import json

import pytest

from src.agent.config import AgentConfig
from src.agent.errors import AgentError
from src.agent.loop import run_analysis, snapshot_tool_schemas
from src.agent.tools import ToolSession, schemas
from tests.agent.test_tools_and_result import snapshot


def test_schemas_only_advertise_captured_ids_without_mutating_registry():
    snap = snapshot()
    tools = {t['function']['name']: t['function']['parameters'] for t in snapshot_tool_schemas(snap)}
    assert tools['get_incident']['properties']['incident_id']['enum'] == [snap.incident_id]
    assert tools['get_asset_policy']['properties']['asset_id']['enum'] == list(snap.policies)
    assert all('enum' not in t['function']['parameters']['properties'].get('incident_id', {}) for t in schemas())
    with pytest.raises(AgentError, match='outside the captured scope'):
        ToolSession(snap).execute('get_incident', json.dumps({'incident_id': 'invented-id'}))


def test_model_receives_exact_incident_id_constraint():
    snap = snapshot()
    class Client:
        async def chat(self, messages, tools, deadline, **kwargs):
            assert len(tools) == 1
            assert tools[0]['function']['parameters']['properties']['incident_id']['enum'] == [snap.incident_id]
            raise AgentError('execution_timeout', 'test deadline')
    with pytest.raises(AgentError) as error:
        asyncio.run(run_analysis(Client(), snap, AgentConfig()))
    assert error.value.code == 'execution_timeout'


def test_absent_snapshot_resources_are_not_advertised():
    from src.agent.providers import FrozenSnapshot
    data = snapshot().export()
    data['policies'] = {}
    data['sensor_health'] = {}
    assert [t['function']['name'] for t in snapshot_tool_schemas(FrozenSnapshot(data))] == ['get_incident']
