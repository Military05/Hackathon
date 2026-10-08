import copy
import json
import time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.ml.features import parse_time
from .errors import AgentError
from .providers import FrozenSnapshot, canonical

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class IncidentArgs(StrictArgs):
    incident_id: Identifier


class PolicyArgs(StrictArgs):
    asset_id: Identifier


class HealthArgs(StrictArgs):
    sensor_id: Identifier


class HistoryArgs(PolicyArgs):
    since: str
    until: str
    limit: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode="after")
    def valid_dates(self):
        if parse_time(self.since) > parse_time(self.until):
            raise ValueError("since must be at or before until")
        return self


REGISTRY = {"get_incident": (IncidentArgs, "Read the incident and captured evidence; call this first."),
            "get_event_history": (HistoryArgs, "Read bounded position/access history from this snapshot."),
            "get_asset_policy": (PolicyArgs, "Read versioned permissions of a registered asset or employee."),
            "get_sensor_health": (HealthArgs, "Read captured sensor status, threshold and last_received_at.")}


def schemas():
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": model.model_json_schema()}} for name, (model, description) in REGISTRY.items()]


class ToolSession:
    def __init__(self, snapshot: FrozenSnapshot):
        self.snapshot = snapshot
        self.trace = []
        self.records = {"event": {}, "sensor_health": {}, "policy": {}, "model_observation": {},
                        'incident_history': {}, 'dispatcher_note': {}}
        self.incident_read = False

    def ref(self, kind, identifier):
        row = self.records[kind][identifier]
        version = row.get("policy_version") if kind == "policy" else row.get("version", f"{kind}-v1")
        return {"kind": kind, "id": identifier, "as_of": self.snapshot.as_of, "version": str(version)}

    def execute(self, name, arguments):
        if name not in REGISTRY:
            raise AgentError("unknown_tool", "Model requested a tool outside the registry.")
        try:
            if not isinstance(arguments, str) or len(arguments) > 4096:
                raise ValueError("Arguments exceed the size limit")
            args = REGISTRY[name][0].model_validate(json.loads(arguments))
        except (ValidationError, ValueError, TypeError) as exc:
            raise AgentError("invalid_tool_arguments", "Tool arguments do not match the schema.") from exc
        started = time.perf_counter()
        data, returned = self.snapshot.data, []
        if name == "get_incident":
            if args.incident_id != self.snapshot.incident_id:
                raise AgentError("not_found", "Incident is outside the captured scope.", 404)
            ids = list(data['incident'].get('evidence_event_ids', []))
            for observation in self.snapshot.observations.values():
                ids.extend(observation['evidence_event_ids'])
            if data.get('history_bounds', {}).get('evidence_selection', {}).get('partial'):
                ids.extend(self.snapshot.events)
            evidence = [self.snapshot.events[i] for i in dict.fromkeys(ids)]
            observations = list(self.snapshot.observations.values())
            self.records["event"].update({e["event_id"]: e for e in evidence})
            self.records["model_observation"].update(self.snapshot.observations)
            returned = [e["event_id"] for e in evidence]
            self.incident_read = True
            context = data.get('incident_context', {})
            self.records['incident_history'].update({row['incident_id']: row for row in context.get('related_incidents', [])})
            self.records['dispatcher_note'].update({row['note_id']: row for row in context.get('dispatcher_notes', [])})
            result = {"incident": data["incident"], "evidence": evidence, "observations": observations,
                      'incident_context': context, 'history_bounds': data.get('history_bounds')}
        elif name == "get_event_history":
            if args.asset_id not in self.snapshot.policies:
                raise AgentError("not_found", "Asset is outside the captured scope.", 404)
            if parse_time(args.until) > parse_time(self.snapshot.as_of):
                raise AgentError("invalid_tool_arguments", "History cannot extend beyond snapshot as_of.")
            events = [e for e in self.snapshot.events.values()
                      if e.get("payload", {}).get("asset_id", e.get("payload", {}).get("employee_id")) == args.asset_id
                      and parse_time(args.since) <= parse_time(e["event_time"]) <= parse_time(args.until)]
            events.sort(key=lambda e: (parse_time(e["event_time"]), e["event_id"]))
            selected = events[:args.limit]
            self.records["event"].update({e["event_id"]: e for e in selected})
            returned = [e["event_id"] for e in selected]
            result = {"events": selected, "truncated": len(events) > args.limit or
                      bool(data.get('history_bounds', {}).get('evidence_selection', {}).get('partial')),
                      "history_bounds": data.get("history_bounds"), "snapshot_bounded": True}
        elif name == "get_asset_policy":
            if args.asset_id not in self.snapshot.policies:
                raise AgentError("not_found", "Asset/employee is outside the captured scope.", 404)
            policy = self.snapshot.policies[args.asset_id]
            if not policy.get("policy_version"):
                raise AgentError("snapshot_invalid", "Policy has no policy_version.")
            self.records["policy"][args.asset_id] = policy
            returned, result = [args.asset_id], {"policy": policy}
        else:
            if args.sensor_id not in self.snapshot.health:
                raise AgentError("not_found", "Sensor is outside the captured scope.", 404)
            health = self.snapshot.health[args.sensor_id]
            self.records["sensor_health"][args.sensor_id] = health
            returned, result = [args.sensor_id], {"sensor_health": health}
        refs = []
        for kind in ("event", "policy", "sensor_health"):
            refs.extend(self.ref(kind, identifier) for identifier in returned if identifier in self.records[kind])
        self.trace.append({"tool": name, "arguments": args.model_dump(), "returned_ids": returned,
                           "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                           "snapshot_id": self.snapshot.snapshot_id})
        return copy.deepcopy({"snapshot": self.snapshot.descriptor(), **result, "evidence_refs": refs})
