import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import AgentError
from .providers import canonical, utc_now
from .tools import Identifier, ToolSession


class FactClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["event", "policy", "sensor_health", "model_observation"]
    id: Identifier
    field: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,3}$")
    value: Any


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=1000)
    confidence: Literal["low", "medium", "high"]
    limitations: list[str] = Field(min_length=1, max_length=5)


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    facts: list[FactClaim] = Field(min_length=1, max_length=20)
    hypotheses: list[Hypothesis] = Field(max_length=5)
    recommendations: list[str] = Field(min_length=1, max_length=8)


def field_value(row, field):
    value = row
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            raise AgentError("invalid_evidence", "Fact refers to a missing field.")
        value = value[part]
    return value


def validate_result(content, session: ToolSession, model_name):
    if not session.incident_read:
        raise AgentError("invalid_evidence", "get_incident was not executed.")
    try:
        # Accept a single JSON code block, but no prose outside it.
        stripped = content.strip()
        if stripped.startswith("```json\n") and stripped.endswith("```"):
            stripped = stripped[8:-3].strip()
        answer = ModelAnswer.model_validate(json.loads(stripped))
    except ValidationError as exc:
        issues = [{"field": ".".join(map(str, error["loc"])), "code": error["type"]}
                  for error in exc.errors(include_url=False, include_input=False)[:5]]
        raise AgentError("model_reply_invalid", "Final answer does not match the verified-facts schema.",
                         details={"issues": issues}) from exc
    except (ValueError, TypeError) as exc:
        raise AgentError("model_reply_invalid", "Final answer does not match the verified-facts schema.",
                         details={"reason": "invalid_json"}) from exc
    facts, refs, event_ids = [], {}, set()
    for claim in answer.facts:
        row = session.records[claim.source].get(claim.id)
        try:
            matches = row is not None and canonical(field_value(row, claim.field)) == canonical(claim.value)
        except (ValueError, TypeError) as exc:
            raise AgentError("invalid_evidence", "Fact value is not finite JSON data.") from exc
        if not matches:
            raise AgentError("invalid_evidence", "Fact ID/field/value was not returned by an executed tool.")
        fact_refs, fact_events = [], []
        if claim.source == "model_observation":
            fact_events = row.get("evidence_event_ids", [])
            if not fact_events or set(fact_events) - session.records["event"].keys():
                raise AgentError("invalid_evidence", "Observation evidence was not read by tools.")
            fact_refs = [session.ref("event", i) for i in fact_events]
        else:
            fact_refs = [session.ref(claim.source, claim.id)]
            if claim.source == "event":
                fact_events = [claim.id]
        event_ids.update(fact_events)
        for ref in fact_refs:
            refs[canonical(ref)] = ref
        # Human-readable measured facts come from verified fields, never model-authored text.
        facts.append({**claim.model_dump(), "text": f"{claim.source} {claim.id}: {claim.field} = {canonical(claim.value)}",
                      "evidence_event_ids": fact_events, "evidence_refs": fact_refs})
    for recommendation in answer.recommendations:
        if not 1 <= len(recommendation) <= 500:
            raise AgentError("model_reply_invalid", "Recommendation length is invalid.")
    descriptor = session.snapshot.descriptor()
    summary = f"Происшествие {session.snapshot.incident_id}. " + "; ".join(f["text"] for f in facts[:3])
    summary += ". Предлагаемое действие оператору: " + answer.recommendations[0]
    return {"summary": summary,
            "facts": facts, "hypotheses": [h.model_dump() for h in answer.hypotheses],
            "recommendations": answer.recommendations, "evidence_event_ids": sorted(event_ids),
            "evidence_refs": list(refs.values()), "tool_trace": list(session.trace),
            "model_name": model_name, "created_at": utc_now(), "incident_snapshot": descriptor,
            "measured_fields": session.snapshot.export()["incident"]}
