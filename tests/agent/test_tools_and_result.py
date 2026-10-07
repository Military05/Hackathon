import asyncio
import copy
import json
from pathlib import Path

import pytest

from src.agent.errors import AgentError
from src.agent.providers import FixtureProvider, FrozenSnapshot
from src.agent.result import validate_result
from src.agent.tools import ToolSession

FIXTURE = Path(__file__).parents[1] / "fixtures" / "agent_v2.json"


def snapshot(incident_id="INC-ZONE-1"):
    record = next(row for row in json.loads(FIXTURE.read_text())["snapshots"]
                  if row["incident"]["incident_id"] == incident_id)
    return FrozenSnapshot(record)


def execute(session, name, **arguments):
    return session.execute(name, json.dumps(arguments))


def answer(facts):
    return json.dumps({"facts": facts, "hypotheses": [{"text": "Возможно, требуется уточнить текущую операцию.",
        "confidence": "low", "limitations": ["Только модельные данные."]}],
        "recommendations": ["Проверить сведения и связаться с ответственным человеком."]})


@pytest.mark.parametrize("name,args,code", [
    ("run_sql", {"sql": "DROP TABLE events"}, "unknown_tool"),
    ("get_incident", {"incident_id": "INC-ZONE-1", "url": "http://example.com"}, "invalid_tool_arguments"),
    ("get_incident", {"incident_id": "missing"}, "not_found"),
    ("get_asset_policy", {"asset_id": "missing"}, "not_found"),
    ("get_sensor_health", {"sensor_id": "missing"}, "not_found"),
    ("get_event_history", {"asset_id": "V1", "since": "2026-10-07T09:00:00Z", "until": "2026-10-07T09:00:10Z", "limit": 101}, "invalid_tool_arguments"),
    ("get_event_history", {"asset_id": "V1", "since": "2026-10-07T09:00:00", "until": "2026-10-07T09:00:10Z", "limit": 10}, "invalid_tool_arguments"),
    ("get_event_history", {"asset_id": "V1", "since": "2026-10-07T09:00:15Z", "until": "2026-10-07T09:00:10Z", "limit": 10}, "invalid_tool_arguments"),
    ("get_event_history", {"asset_id": "V1", "since": "2026-10-07T09:00:00Z", "until": "2026-10-07T09:00:30Z", "limit": 10}, "invalid_tool_arguments"),
    ("get_event_history", {"asset_id": "V1", "since": "2026-10-07T09:00:00Z", "until": "2026-10-07T09:00:10Z", "limit": True}, "invalid_tool_arguments"),
])
def test_registry_rejects_invalid_or_out_of_scope_calls(name, args, code):
    session = ToolSession(snapshot())
    with pytest.raises(AgentError) as error:
        session.execute(name, json.dumps(args))
    assert error.value.code == code
    assert session.trace == []


def test_correct_id_does_not_authorize_false_value_or_text():
    session = ToolSession(snapshot())
    execute(session, "get_incident", incident_id="INC-ZONE-1")
    claim = {"source": "event", "id": "demo-position-0010", "field": "payload.x", "value": 999}
    with pytest.raises(AgentError, match="Fact ID/field/value"):
        validate_result(answer([claim]), session, "test-double")
    claim["value"] = 20.0
    result = validate_result(answer([claim]), session, "test-double")
    assert "999" not in result["facts"][0]["text"]
    assert result["evidence_event_ids"] == ["demo-position-0010"]
    claim["text"] = "Машина находится в другом месте."
    with pytest.raises(AgentError):
        validate_result(answer([claim]), session, "test-double")


def test_policy_requires_an_executed_policy_tool_and_version():
    session = ToolSession(snapshot())
    execute(session, "get_incident", incident_id="INC-ZONE-1")
    claim = {"source": "policy", "id": "U1", "field": "allowed_buildings", "value": ["O1"]}
    with pytest.raises(AgentError):
        validate_result(answer([claim]), session, "test-double")
    execute(session, "get_asset_policy", asset_id="U1")
    result = validate_result(answer([claim]), session, "test-double")
    assert result["evidence_refs"][0]["version"] == "policy-v1"
    assert result["evidence_event_ids"] == []


def test_never_started_has_health_evidence_without_invented_event():
    session = ToolSession(snapshot("INC-NEVER-STARTED"))
    execute(session, "get_incident", incident_id="INC-NEVER-STARTED")
    execute(session, "get_sensor_health", sensor_id="POS-V2")
    result = validate_result(answer([{"source": "sensor_health", "id": "POS-V2", "field": "last_received_at", "value": None}]),
                             session, "test-double")
    assert result["evidence_event_ids"] == []
    assert result["evidence_refs"][0]["kind"] == "sensor_health"


def test_snapshot_is_frozen_and_owner_changes_do_not_change_analytical_hash():
    original = snapshot()
    data = original.export()
    data["as_of"] = "2026-10-07T09:00:21Z"
    data["incident"]["assigned_operator_id"] = "dispatcher-3"
    data["incident"]["dispatch_revision"] = 99
    assert FrozenSnapshot(data).snapshot_id == original.snapshot_id
    data["events"][0]["payload"]["x"] = 21
    assert FrozenSnapshot(data).snapshot_id != original.snapshot_id
    session = ToolSession(original)
    returned = execute(session, "get_incident", incident_id="INC-ZONE-1")
    returned["evidence"][0]["payload"]["x"] = 500
    assert execute(session, "get_incident", incident_id="INC-ZONE-1")["evidence"][0]["payload"]["x"] == 20
    data = original.export()
    data["policies"]["V1"]["policy_version"] = "new-policy"
    assert FrozenSnapshot(data).snapshot_id != original.snapshot_id


def test_evidence_must_exist_in_capture():
    data = snapshot().export()
    data["incident"]["evidence_event_ids"] = ["invented"]
    with pytest.raises(AgentError):
        FrozenSnapshot(data)
