"""Backend captures a short transaction; every tool then reads its materialized copy."""
import copy
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

from src.ml.features import iso, parse_time
from .errors import AgentError

OWNERSHIP_FIELDS = {"assigned_operator_id", "requested_by_operator_id", "dispatch_revision",
                    "pending_transfer", "dispatch_history", "notifications", "acknowledged_at",
                    "acknowledged_by", "status", "revision", "escalation_level", "updated_at"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FrozenSnapshot:
    def __init__(self, data):
        try:
            encoded = canonical(data)
            if len(encoded.encode()) > 256_000:
                raise ValueError("Snapshot too large; capture bounded history")
            self.data = json.loads(encoded)
            parse_time(self.data["as_of"])
            incident = self.data["incident"]
            if not isinstance(incident.get("incident_id"), str):
                raise ValueError("Missing incident_id")
            for key in ("events", "observations"):
                rows = self.data.setdefault(key, [])
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError(f"{key} must be a bounded list of at most 100 records")
            self.events = self._index("events", "event_id")
            self.observations = self._index("observations", "observation_id")
            self.policies = self.data.setdefault("policies", {})
            self.health = self.data.setdefault("sensor_health", {})
            if not isinstance(self.policies, dict) or not isinstance(self.health, dict):
                raise ValueError("policies/sensor_health must be dictionaries keyed by registered IDs")
            evidence = incident.get("evidence_event_ids", [])
            if not isinstance(evidence, list) or set(evidence) - self.events.keys():
                raise ValueError("Incident evidence must exist in the captured events")
            for event in self.events.values():
                parse_time(event["event_time"])
                if event.get("received_at") and parse_time(event["received_at"]) > parse_time(self.data["as_of"]):
                    raise ValueError("Snapshot contains events received after as_of")
            analytical = copy.deepcopy(self.data)
            analytical.pop("as_of", None)
            analytical.pop("snapshot_id", None)
            analytical["incident"] = {k: v for k, v in incident.items() if k not in OWNERSHIP_FIELDS}
            for row in analytical["sensor_health"].values():
                row.pop("as_of", None)
            self.snapshot_id = "snap-" + hashlib.sha256(canonical(analytical).encode()).hexdigest()
            # Never trust a caller-supplied hash.
            self.data["snapshot_id"] = self.snapshot_id
        except (ValueError, KeyError, TypeError) as exc:
            raise AgentError("snapshot_invalid", str(exc), 422) from exc

    def _index(self, key, id_field):
        result = {}
        for row in self.data[key]:
            identifier = row[id_field]
            if not isinstance(identifier, str) or identifier in result:
                raise ValueError(f"Invalid/duplicate {id_field}")
            result[identifier] = row
        return result

    @property
    def incident_id(self):
        return self.data["incident"]["incident_id"]

    @property
    def as_of(self):
        return self.data["as_of"]

    def descriptor(self):
        return {"snapshot_id": self.snapshot_id, "as_of": self.as_of,
                "rule_version": self.data.get("rule_version"), "model_version": self.data.get("model_version"),
                "policy_versions": {k: v.get("policy_version") for k, v in self.policies.items()},
                "evidence_event_ids": list(self.data["incident"].get("evidence_event_ids", []))}

    def export(self):
        return copy.deepcopy(self.data)


class CaptureProvider:
    """Inject A1's single-transaction query. No live queries are executed by tools."""
    def __init__(self, capture):
        self.capture = capture

    async def snapshot(self, incident_id):
        # The callback must be async or fast. A1 can wrap synchronous SQLite reads in to_thread.
        value = self.capture(incident_id)
        if inspect.isawaitable(value):
            value = await value
        result = value if isinstance(value, FrozenSnapshot) else FrozenSnapshot(value)
        if result.incident_id != incident_id:
            raise AgentError("snapshot_invalid", "Capture returned a different incident.")
        return result


class FixtureProvider:
    """Explicit development provider, never a silent fallback for a missing backend."""
    def __init__(self, path):
        self.path = Path(path)

    async def snapshot(self, incident_id):
        data = json.loads(self.path.read_text())
        records = data.get("snapshots", [data])
        for record in records:
            if record.get("incident", {}).get("incident_id") == incident_id:
                return FrozenSnapshot(record)
        raise AgentError("not_found", "Incident is not in the development fixture.", 404)


def utc_now():
    return iso(datetime.now(timezone.utc))
