"""Real HTTP backend, SQLite, transferred MLP and local Qwen; isolated synthetic D4."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from src.agent.config import load_env_file
from src.agent.model_client import LocalModelClient
from src.core.service import stamp
from src.ml.features import parse_time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--output", default=str(ROOT / "artifacts/local/qwen-backend-live.json"))
    parser.add_argument("--scenario", choices=("d4", "collision"), default="d4")
    args = parser.parse_args()
    load_env_file(args.env_file)
    os.environ.update(DISPATCH_ENABLE_AUTH="1", DISPATCH_ENABLE_AGENT="1", DISPATCH_ENABLE_ML="1",
                      DISPATCH_ENABLE_DEMO_TRAFFIC="0", DISPATCH_SOURCE_KEY=secrets.token_urlsafe(32))
    report = {"status": "FAIL", "live_backend": True, "real_model_requests": True,
              "transport": "loopback HTTP via uvicorn", "data": "explicit synthetic " + args.scenario,
              "scheduler": False, "inference": "real adapter on stored events, invoked directly",
              "checked_at": stamp()}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hackathon-ai-live-") as temp:
        from src.core.main import create_app
        app = create_app(db_path=Path(temp) / "dispatch.db", enable_scheduler=False)
        replies = []

        class RecordingClient(LocalModelClient):
            async def chat(self, *arguments, **keywords):
                reply = await super().chat(*arguments, **keywords)
                replies.append(reply)
                return reply

        app.state.agent.client = RecordingClient(app.state.agent.config)
        password = secrets.token_urlsafe(24)
        app.state.auth.create_user("live.acceptance", "Проверка AI", password,
                                  operator_id="dispatcher-1", status="active")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", proxy_headers=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and time.monotonic() < deadline:
                time.sleep(.05)
            if not server.started:
                raise RuntimeError("Backend не запустился")
            port = server.servers[0].sockets[0].getsockname()[1]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
                login = client.post("/api/auth/login", json={"username": "live.acceptance", "password": password})
                login.raise_for_status()
                headers = {"X-CSRF-Token": login.json()["csrf_token"],
                           "X-Expected-User": login.json()["user"]["id"]}
                end = datetime.now(timezone.utc)
                end = end.fromtimestamp(int(end.timestamp()) // 5 * 5, tz=timezone.utc) - timedelta(seconds=5)
                if args.scenario == "d4":
                    episode = json.loads((ROOT / "artifacts/local/d4-episode.json").read_text(encoding="utf-8"))
                    original = json.loads((ROOT / "artifacts/local/d4-agent-snapshot.json").read_text(encoding="utf-8"))["observations"][0]
                    shift = end - parse_time(original["window_end"])
                    events = [event for event in episode["events"] if parse_time(event["event_time"]) <= parse_time(original["window_end"])]
                else:
                    end = datetime.now(timezone.utc) - timedelta(milliseconds=100)
                    shift = timedelta(0)
                    events = [{"event_id": "live-pair-" + asset, "event_time": stamp(end),
                               "sensor_id": "POS-" + asset, "type": "position", "demo": True,
                               "payload": {"asset_id": asset, "x": 40, "y": y}}
                              for asset, y in (("V1", 74), ("V3", 74.5))]
                for event in events:
                    event = {**event, "event_time": stamp(parse_time(event["event_time"]) + shift)}
                    response = client.post("/api/events", json=event, headers={"X-Source-Key": os.environ["DISPATCH_SOURCE_KEY"]})
                    response.raise_for_status()
                backend = app.state.service
                observation = None
                if args.scenario == "d4":
                    saved = backend.event_history("V1", stamp(end-timedelta(seconds=10)), stamp(end), 100)
                    observation = app.state.ml.evaluate("V1", saved, stamp(end))
                    backend.register_model_observation(observation)
                    if observation["status"] != "anomaly":
                        raise RuntimeError("Перенесённая MLP не подтвердила D4")
                incident = next(item for item in backend.list_incidents()
                                if item["type"] == ("model_anomaly" if args.scenario == "d4" else "collision"))
                response = client.post(f"/api/incidents/{incident['incident_id']}/analysis", headers=headers)
                response.raise_for_status()
                job_id = response.json()["job_id"]
                deadline = time.monotonic() + 65
                while time.monotonic() < deadline:
                    response = client.get(f"/api/agent-jobs/{job_id}", headers={"X-Expected-User": headers["X-Expected-User"]})
                    response.raise_for_status()
                    job = response.json()
                    if job["status"] in ("completed", "failed"):
                        break
                    time.sleep(.2)
                report.update(observation=observation, incident_id=incident["incident_id"], job=job,
                              stored_events=len(events), auth_enabled=client.get("/api/health").json()["auth"]["enabled"])
                if job["status"] != "completed":
                    raise RuntimeError("Анализ Qwen завершился ошибкой: " + json.dumps(job.get("error"), ensure_ascii=False))
                if args.scenario == "d4":
                    verified = {fact["field"] for fact in job["result"]["facts"]
                                if fact["source"] == "model_observation" and fact["id"] == observation["observation_id"]}
                    if not {"status", "score", "threshold"} <= verified:
                        raise RuntimeError("В отчёте нет обязательных фактов MLP")
                elif not {event["event_id"] for event in events} <= set(job["result"]["evidence_event_ids"]):
                    raise RuntimeError("В отчёте нет доказательств обеих машин")
                report["status"] = "PASS"
        except Exception as error:
            report["error"] = str(error)
        finally:
            report["diagnostic_model_replies"] = replies
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Backend проверки не завершился корректно")
    report["seconds"] = time.monotonic() - started
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("status", "live_backend", "seconds", "error")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
