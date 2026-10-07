import hashlib
import json

import joblib
import numpy as np
import pytest

from src.ml.dataset import matrix, read_episodes
from src.ml.demo import export_demo
from src.ml.evaluate import evaluate
from src.ml.generate import generate
from src.ml.inference import MovementModel
from src.ml.train import train


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("movement-experiment")
    data, artifacts = root / "data", root / "artifacts"
    generate(data, episodes_per_class=20)
    metadata = train(data, artifacts)
    return data, artifacts, metadata


def test_reproducible_generation_and_split_isolation(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert generate(a, episodes_per_class=20) == generate(b, episodes_per_class=20)
    assert (a / "episodes.jsonl").read_bytes() == (b / "episodes.jsonl").read_bytes()
    episodes = read_episodes(a)
    sets = [{e["episode_id"] for e in episodes if e["split"] == split} for split in ("train", "validation", "test")]
    assert not sets[0] & sets[1] and not sets[0] & sets[2] and not sets[1] & sets[2]
    assert {e["scenario"] for e in episodes if not e["label"]} >= {"loading", "waiting", "turns"}
    buildings = [(10, 10, 20, 15), (60, 10, 20, 15), (10, 50, 25, 20), (55, 50, 25, 20), (35, 75, 20, 12), (5, 80, 12, 10)]
    for episode in episodes:
        for event in episode["events"]:
            p = event["payload"]
            assert not any(x < p["x"] < x + w and y < p["y"] < y + h for x, y, w, h in buildings)


def test_scaler_only_train_and_pipeline_shared_with_serving(trained):
    data, artifacts, metadata = trained
    x, _, _ = matrix(read_episodes(data), "train")
    model = MovementModel(artifacts / "movement.joblib")
    assert model.state == "ready"
    assert model.pipeline["scaler"].n_samples_seen_ == len(x)
    np.testing.assert_allclose(model.pipeline["scaler"].mean_, x.mean(axis=0))
    assert metadata["training"]["test_used_for_training"] is False
    assert model.pipeline["mlp"].hidden_layer_sizes == (16, 8)
    assert metadata["classes"] == [0, 1]
    assert model.observe([], "V1", "2026-10-07T09:00:10Z")["status"] == "insufficient_data"
    report = evaluate(data, artifacts / "movement.joblib")
    assert report["mlp"]["windows"] > 0 and "false_incidents" in report["mlp"]
    assert report["test_used_for_threshold"] is False
    exported = export_demo(data, artifacts / "movement.joblib", artifacts)
    assert exported["first_anomaly"]["status"] == "anomaly"
    assert not exported["backend_incident_created"]


def test_artifact_corruption_and_version_mismatch_fail_explicitly(trained, tmp_path):
    _, artifacts, metadata = trained
    dest = tmp_path / "movement.joblib"
    dest.write_bytes((artifacts / "movement.joblib").read_bytes() + b"damage")
    dest.with_suffix(".metadata.json").write_text(json.dumps(metadata))
    assert MovementModel(dest).state == "artifact_invalid"
    dest.write_bytes((artifacts / "movement.joblib").read_bytes())
    wrong = dict(metadata, feature_names=list(reversed(metadata["feature_names"])))
    dest.with_suffix(".metadata.json").write_text(json.dumps(wrong))
    assert MovementModel(dest).state == "artifact_invalid"


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
def test_trained_observation_flows_to_verified_agent_result(trained, provider):
    """Real trained MLP + real tools/validator; language-model response is a test double."""
    import asyncio
    import httpx
    from src.agent.config import AgentConfig
    from src.agent.loop import run_analysis
    from src.agent.model_client import LocalModelClient
    from src.agent.providers import FrozenSnapshot
    from src.agent.tools import ToolSession
    data, artifacts, _ = trained
    export_demo(data, artifacts / "movement.joblib", artifacts)
    snap = FrozenSnapshot(json.loads((artifacts / "d4-agent-snapshot.json").read_text()))
    observation = snap.data["observations"][0]
    wire = []

    def transport(request):
        body = json.loads(request.content)
        wire.append(body)
        if len(wire) < 3:
            name, args = (("get_incident", {"incident_id": snap.incident_id}) if len(wire) == 1
                          else ("get_asset_policy", {"asset_id": "V1"}))
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"d4-call-{len(wire)}", "type": "function", "function": {"name": name,
                 "arguments": args if provider == "ollama" else json.dumps(args)}}]}
        else:
            assert not body["tools"]
            assert (body.get("format") if provider == "ollama" else body.get("response_format"))
            message = {"role": "assistant", "content": json.dumps({"facts": [
                {"source": "model_observation", "id": observation["observation_id"],
                 "field": field, "value": observation[field]} for field in ("score", "threshold", "status")],
                "hypotheses": [], "recommendations": ["Проверить, не выполняется ли штатная погрузка."]})}
        return httpx.Response(200, json={"message": message} if provider == "ollama"
                              else {"choices": [{"message": message}]})

    async def run():
        config = AgentConfig(provider=provider, model="language-test-double",
                             base_url="http://127.0.0.1:11434" if provider == "ollama" else "http://127.0.0.1:1234/v1")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            return await run_analysis(client, snap, config)
        finally:
            await client.close()

    result = asyncio.run(run())
    assert len(wire) == 3
    assert json.loads(wire[0]["messages"][1]["content"])["snapshot"] == snap.descriptor()
    tool_messages = [m for m in wire[-1]["messages"] if m["role"] == "tool"]
    incident_data, policy_data = [json.loads(m["content"]) for m in tool_messages]
    assert incident_data["incident"] == snap.data["incident"]
    assert incident_data["evidence"] == [snap.events[i] for i in observation["evidence_event_ids"]]
    assert incident_data["observations"] == snap.data["observations"]
    assert policy_data["policy"] == snap.policies["V1"]
    assert all("snapshot" not in payload and "evidence_refs" not in payload
               for payload in (incident_data, policy_data))
    # D4 must retain every raw event and exact MLP value while removing significant
    # repeated metadata from the actual request, rather than trimming the fixture.
    full_session = ToolSession(snap)
    legacy_chars = sum(len(json.dumps(full_session.execute(name, json.dumps(args)), ensure_ascii=False))
                       for name, args in (("get_incident", {"incident_id": snap.incident_id}),
                                          ("get_asset_policy", {"asset_id": "V1"})))
    assert sum(len(m["content"]) for m in tool_messages) < .7 * legacy_chars
    assert result["facts"][0]["value"] == observation["score"]
    assert result["facts"][1]["value"] == observation["threshold"]
    assert result["evidence_event_ids"] == sorted(observation["evidence_event_ids"])
    assert {ref["id"] for ref in result["evidence_refs"] if ref["kind"] == "event"} == set(observation["evidence_event_ids"])
    assert all(ref["as_of"] == snap.as_of for ref in result["evidence_refs"])
    assert [row["tool"] for row in result["tool_trace"]] == ["get_incident", "get_asset_policy"]
    assert result["incident_snapshot"]["model_version"] == snap.data["model_version"]
