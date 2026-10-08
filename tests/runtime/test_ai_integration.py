"""Real v5 SQLite + trained MLP + authenticated jobs; LLM transport is a test double."""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

try:
    from src.ml.generate import generate
    from src.ml.train import train
except ImportError as error:
    raise unittest.SkipTest("AI-зависимости не установлены: requirements-ai-dev.txt") from error

from src.agent.backend import capture_snapshot
from src.agent.config import AgentConfig
from src.agent.errors import AgentError
from src.agent.model_client import LocalModelClient
from src.agent.loop import run_analysis
from src.agent.providers import FrozenSnapshot
from src.agent.result import FactClaim, validate_result
from src.agent.tools import ToolSession
from src.core.main import create_app
from src.core.service import Service, stamp
from src.ml.dataset import episode_windows, read_episodes
from src.ml.movement import MovementModel

ROOT = Path(__file__).resolve().parents[2]


class AIIntegrationTests(unittest.TestCase):
    def test_repository_model_is_ready_without_local_transfer(self):
        with patch.dict(os.environ, {}, clear=True):
            model = MovementModel()
        self.assertEqual(model.health()["status"], "ready")
        self.assertEqual(model.artifact, ROOT / "models/movement-v1/movement.joblib")

    @classmethod
    def setUpClass(cls):
        cls.experiment = tempfile.TemporaryDirectory()
        cls.data = Path(cls.experiment.name) / "data"
        cls.artifacts = Path(cls.experiment.name) / "artifacts"
        generate(cls.data, episodes_per_class=20)
        train(cls.data, cls.artifacts)
        cls.artifact = cls.artifacts / "movement.joblib"
        probe_model = MovementModel(artifact=cls.artifact)
        cls.episode, cls.window_end = next(
            (episode, window.window_end)
            for episode in read_episodes(cls.data) if episode["split"] == "test" and episode["label"]
            for window, label in episode_windows(episode)
            if label == 1 and probe_model.observe(episode["events"], "V1", window.window_end)["status"] == "anomaly")

    @classmethod
    def tearDownClass(cls):
        cls.experiment.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime.fromisoformat(self.window_end.replace("Z", "+00:00"))
        self.service = Service(Path(self.temp.name) / "dispatch.db", ROOT / "data/demo/site.json", clock=lambda: self.now)
        self.events = [event for event in self.episode["events"]
                       if datetime.fromisoformat(event["event_time"].replace("Z", "+00:00")) <= self.now]
        for event in self.events:
            self.service.ingest_event(event)
        self.model = MovementModel(self.service, self.artifact)
        self.observation = self.model.evaluate("V1", self.events, self.window_end)
        self.service.register_model_observation(self.observation)
        self.incident = next(item for item in self.service.list_incidents() if item["type"] == "model_anomaly")

    def tearDown(self):
        self.temp.cleanup()

    def test_real_mlp_evidence_snapshot_and_committed_watermark_survive_restart(self):
        snapshot = FrozenSnapshot(capture_snapshot(self.service, self.incident["incident_id"]))
        self.assertIn(self.observation["observation_id"], snapshot.observations)
        self.assertLessEqual(len(snapshot.events), 100)
        self.assertTrue(set(self.observation["evidence_event_ids"]) <= snapshot.events.keys())
        # A late position cannot rewrite an already published observation.
        late = {**self.events[-1], "event_id": "late-window-position",
                "payload": {"asset_id": "V1", "x": 45, "y": 40}}
        self.service.ingest_event(late)
        resumed = Service(self.service.store.path, ROOT / "data/demo/site.json", clock=lambda: self.now)
        model = MovementModel(resumed, self.artifact)
        again = model.evaluate("V1", self.events + [late], self.window_end)
        self.assertEqual(again, self.observation)
        resumed.register_model_observation(again)
        self.assertEqual(len(resumed.model_observations("V1")), 1)

    def test_v6_collision_requires_evidence_and_policies_of_both_vehicles(self):
        service = Service(Path(self.temp.name) / "collision.db", ROOT / "data/demo/site.json", clock=lambda: self.now)
        for asset, y in (("V1", 74), ("V3", 74.5)):
            service.ingest_event({"event_id": "pair-" + asset, "event_time": stamp(self.now),
                                  "sensor_id": "POS-" + asset, "type": "position", "demo": True,
                                  "payload": {"asset_id": asset, "x": 40, "y": y}})
        incident = next(item for item in service.list_incidents() if item["type"] == "collision")
        session = ToolSession(FrozenSnapshot(capture_snapshot(service, incident["incident_id"])))
        self.assertEqual(set(session.snapshot.policies), {"V1", "V3"})
        session.execute("get_incident", json.dumps({"incident_id": incident["incident_id"]}))
        facts = [{"source": "event", "id": event["event_id"], "field": "payload.x", "value": event["payload"]["x"]}
                 for event in session.records["event"].values()]
        answer = {"facts": facts[:1], "hypotheses": [], "recommendations": ["Проверьте измеренное сближение обеих машин."]}
        with self.assertRaises(AgentError) as caught:
            validate_result(json.dumps(answer), session, "test-model")
        self.assertEqual(caught.exception.code, "invalid_evidence")
        answer["facts"] = facts
        result = validate_result(json.dumps(answer), session, "test-model")
        self.assertEqual(set(result["evidence_event_ids"]), {"pair-V1", "pair-V3"})

    def test_polling_time_does_not_make_snapshot_stale_but_new_measurement_does(self):
        before = FrozenSnapshot(capture_snapshot(self.service, self.incident["incident_id"]))
        self.now += timedelta(seconds=1)
        after = FrozenSnapshot(capture_snapshot(self.service, self.incident["incident_id"]))
        self.assertEqual(before.snapshot_id, after.snapshot_id)
        event = {"event_id": "new-position", "sensor_id": "POS-V1", "event_time": stamp(self.now),
                 "type": "position", "demo": True, "payload": {"asset_id": "V1", "x": 45, "y": 40}}
        self.service.ingest_event(event)
        updated = FrozenSnapshot(capture_snapshot(self.service, self.incident["incident_id"]))
        self.assertNotEqual(before.snapshot_id, updated.snapshot_id)

    def test_retrained_version_does_not_reuse_previous_model_watermark(self):
        upgraded = MovementModel(self.service, self.artifact)
        upgraded.metadata = {**upgraded.metadata, "model_version": "retrained-test-version"}
        result = upgraded.evaluate("V1", self.events, self.window_end)
        self.assertEqual(result["model_version"], "retrained-test-version")
        self.assertNotEqual(result["observation_id"], self.observation["observation_id"])
        self.service.register_model_observation(result)
        late = {**self.events[-1], "event_id": "late-after-upgrade",
                "payload": {"asset_id": "V1", "x": 45, "y": 40}}
        self.service.ingest_event(late)
        self.assertEqual(upgraded.evaluate("V1", self.events + [late], self.window_end), result)
        self.assertEqual(len(self.service.model_observations("V1")), 2)

    def test_model_report_requires_the_linked_score_threshold_status(self):
        session = ToolSession(FrozenSnapshot(capture_snapshot(self.service, self.incident["incident_id"])))
        session.execute("get_incident", json.dumps({"incident_id": self.incident["incident_id"]}))
        event = next(iter(session.records["event"].values()))
        answer = {"facts": [{"source": "event", "id": event["event_id"], "field": "payload.x", "value": event["payload"]["x"]}],
                  "hypotheses": [], "recommendations": ["Проверить движение."]}
        with self.assertRaises(AgentError) as error:
            validate_result(json.dumps(answer), session, "explicit-model-test-double")
        self.assertEqual(error.exception.code, "invalid_evidence")
        answer["facts"] = [{"source": "model_observation", "id": self.observation["observation_id"],
                            "field": field, "value": self.observation[field]}
                           for field in ("status", "score", "threshold")]
        self.assertEqual(len(validate_result(json.dumps(answer), session, "explicit-model-test-double")["facts"]), 3)

    def test_http_timeout_is_execution_timeout(self):
        def timed_out(request):
            raise httpx.ReadTimeout("explicit timeout fixture", request=request)

        async def run():
            client = LocalModelClient(AgentConfig(model="explicit-model-test-double"), httpx.MockTransport(timed_out))
            try:
                await client.chat([], [], time.monotonic() + 60)
            finally:
                await client.close()
        with self.assertRaises(AgentError) as error:
            asyncio.run(run())
        self.assertEqual(error.exception.code, "execution_timeout")

    def test_numeric_strings_are_not_silently_converted_to_measurements(self):
        kinds = {branch["type"] for branch in FactClaim.model_json_schema()["properties"]["value"]["anyOf"]}
        self.assertTrue({"number", "integer", "boolean", "string", "null", "array"} <= kinds)
        session = ToolSession(FrozenSnapshot(capture_snapshot(self.service, self.incident["incident_id"])))
        session.execute("get_incident", json.dumps({"incident_id": self.incident["incident_id"]}))
        claim = {"source": "model_observation", "id": self.observation["observation_id"],
                 "field": "score", "value": str(self.observation["score"])}
        with self.assertRaises(AgentError) as error:
            validate_result(json.dumps({"facts": [claim], "hypotheses": [], "recommendations": ["Проверить движение."]}),
                            session, "explicit-model-test-double")
        self.assertEqual(error.exception.code, "invalid_evidence")

    def test_sensor_schema_forces_tools_and_discards_unverified_tool_prose(self):
        fixture = json.loads((ROOT / "tests/fixtures/agent_v2.json").read_text(encoding="utf-8"))
        snapshot = FrozenSnapshot(next(row for row in fixture["snapshots"]
                                       if row["incident"]["incident_id"] == "INC-NEVER-STARTED"))
        wire = []

        def transport(request):
            body = json.loads(request.content)
            wire.append(body)
            if len(wire) < 3:
                self.assertEqual(body["tool_choice"], "required")
                name = "get_incident" if len(wire) == 1 else "get_sensor_health"
                args = {"incident_id": snapshot.incident_id} if len(wire) == 1 else {"sensor_id": "POS-V2"}
                message = {"role": "assistant", "content": "UNVERIFIED TOOL PROSE",
                           "tool_calls": [{"id": f"call-{len(wire)}", "type": "function",
                           "function": {"name": name, "arguments": json.dumps(args)}}]}
            else:
                self.assertTrue(all("UNVERIFIED TOOL PROSE" not in row.get("content", "") for row in body["messages"]))
                properties = body["response_format"]["json_schema"]["schema"]["$defs"]["FactClaim"]["properties"]
                self.assertEqual(properties["source"]["enum"], ["sensor_health"])
                self.assertEqual(properties["id"]["enum"], ["POS-V2"])
                sensor = snapshot.health["POS-V2"]
                answer = {"facts": [{"source": "sensor_health", "id": "POS-V2", "field": field,
                                     "value": sensor[field]} for field in ("status", "last_received_at", "threshold_seconds")],
                          "hypotheses": [], "recommendations": ["Проверить подключение датчика."]}
                message = {"role": "assistant", "content": json.dumps(answer)}
            return httpx.Response(200, json={"choices": [{"message": message}]})

        async def run():
            config = AgentConfig(model="explicit-model-test-double")
            client = LocalModelClient(config, httpx.MockTransport(transport))
            try:
                return await run_analysis(client, snapshot, config)
            finally:
                await client.close()
        result = asyncio.run(run())
        self.assertEqual(len(wire), 3)
        self.assertEqual(len(result["facts"]), 3)
        self.assertEqual(result["evidence_event_ids"], [])

    def test_authenticated_v5_job_uses_session_and_shared_sqlite(self):
        calls = []

        def transport(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "explicit-model-test-double"}]})
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(body["max_tokens"], 1024 if body.get('response_format') else 450)
            if len(calls) == 1:
                message = {"role": "assistant", "content": "", "tool_calls": [{"id": "real-python-tool",
                    "type": "function", "function": {"name": "get_incident",
                    "arguments": json.dumps({"incident_id": self.incident["incident_id"]})}}]}
            else:
                message = {"role": "assistant", "content": json.dumps({"facts": [
                    {"source": "model_observation", "id": self.observation["observation_id"],
                     "field": field, "value": self.observation[field]} for field in ("status", "score", "threshold")],
                    "hypotheses": [], "recommendations": ["Проверить, нет ли штатной погрузки."]})}
            return httpx.Response(200, json={"choices": [{"message": message}]})

        with patch.dict(os.environ, {"DISPATCH_ENABLE_AGENT": "1", "DISPATCH_ENABLE_ML": "1",
                "DISPATCH_ML_ARTIFACT": str(self.artifact), "DISPATCH_ENABLE_AUTH": "1",
                "DISPATCH_ENABLE_DEMO_TRAFFIC": "0", "LOCAL_LLM_MODEL": "explicit-model-test-double"}):
            app = create_app(db_path=self.service.store.path, enable_scheduler=False, clock=lambda: self.now)
            app.state.agent.client = LocalModelClient(app.state.agent.config, httpx.MockTransport(transport))
            app.state.auth.create_user("integration.operator", "Оператор", "Integration-password-2026",
                                       operator_id="dispatcher-1", status="active")
            with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50100)) as client:
                url = f"/api/incidents/{self.incident['incident_id']}/analysis"
                self.assertEqual(client.post(url, headers={"X-Demo-Operator": "dispatcher-3"}).status_code, 401)
                login = client.post("/api/auth/login", json={"username": "integration.operator", "password": "Integration-password-2026"})
                self.assertEqual(login.status_code, 200)
                self.assertEqual(client.post(url).status_code, 403)
                headers = {"X-CSRF-Token": login.json()["csrf_token"], "X-Demo-Operator": "dispatcher-3"}
                response = client.post(url, headers=headers)
                self.assertEqual(response.status_code, 202, response.text)
                job_id = response.json()["job_id"]
                self.assertEqual(response.json()["requested_by_operator_id"], "dispatcher-1")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    job = client.get(f"/api/agent-jobs/{job_id}").json()
                    if job["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(job["status"], "completed", job)
                self.assertFalse(job["stale"])
                self.assertEqual(len(job["result"]["facts"]), 3)
                cached = client.post(url, headers=headers).json()
                self.assertTrue(cached["cached"])
                self.assertEqual(cached["job_id"], job_id)
                self.assertEqual(len(calls), 3)
                self.assertIn('response_format', calls[-1])
                with self.service.store.read() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM b1_agent_jobs").fetchone()[0], 1)
                self.assertEqual(client.get(f"/api/agent-jobs/{job_id}", headers={"X-Expected-User": "old-tab"}).status_code, 409)
            self.assertIsNone(app.state.agent.worker)

    def test_missing_qwen_does_not_disable_monitoring(self):
        with patch.dict(os.environ, {"DISPATCH_ENABLE_AGENT": "1", "DISPATCH_ENABLE_ML": "1",
                "DISPATCH_ML_ARTIFACT": str(self.artifact), "LOCAL_LLM_MODEL": "",
                "DISPATCH_ENABLE_DEMO_TRAFFIC": "0"}):
            app = create_app(db_path=self.service.store.path, enable_scheduler=False, enable_auth=False, clock=lambda: self.now)
            with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50100)) as client:
                self.assertEqual(client.get("/api/health").json()["ml"]["status"], "ready")
                self.assertEqual(client.get("/api/health").json()["agent"]["status"], "unavailable")
                response = client.post(f"/api/incidents/{self.incident['incident_id']}/analysis",
                                       headers={"X-Demo-Operator": "dispatcher-1"})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(client.get("/api/site").status_code, 200)
                self.assertEqual(client.get("/api/model-observations").status_code, 200)


if __name__ == "__main__":
    unittest.main()
