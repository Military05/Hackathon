"""Real HTTP backend, SQLite, transferred MLP and local Qwen; isolated synthetic D4."""
import argparse
import json
import os
import re
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
    parser.add_argument("--scenario", choices=("d4", "collision", "forbidden-zone"), default="d4")
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
        # The module-level app also boots on import: keep it away from real accounts.
        os.environ["DISPATCH_DB"] = str(Path(temp) / "import-bootstrap.db")
        from src.core.main import create_app
        clock_now = datetime.now(timezone.utc)
        app = create_app(db_path=Path(temp) / "dispatch.db", enable_scheduler=False, clock=lambda: clock_now)
        report['clock'] = 'controlled test timeline; advanced only for new measurements'
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
                end = clock_now
                end = end.fromtimestamp(int(end.timestamp()) // 5 * 5, tz=timezone.utc) - timedelta(seconds=5)
                if args.scenario == "d4":
                    episode = json.loads((ROOT / "artifacts/local/d4-episode.json").read_text(encoding="utf-8"))
                    original = json.loads((ROOT / "artifacts/local/d4-agent-snapshot.json").read_text(encoding="utf-8"))["observations"][0]
                    shift = end - parse_time(original["window_end"])
                    events = [event for event in episode["events"] if parse_time(event["event_time"]) <= parse_time(original["window_end"])]
                elif args.scenario == 'forbidden-zone':
                    end = clock_now - timedelta(milliseconds=100)
                    shift = timedelta(0)
                    zone = next(row for row in app.state.service.site['zones'] if row['id'] == 'Z1')
                    rect = zone['rectangle']
                    events = [{'event_id': 'live-zone-position', 'event_time': stamp(end),
                               'sensor_id': 'POS-V1', 'type': 'position', 'demo': True,
                               'payload': {'asset_id': 'V1', 'x': rect['x'] + rect['width'] / 2,
                                           'y': rect['y'] + rect['height'] / 2}}]
                else:
                    end = clock_now - timedelta(milliseconds=100)
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
                                if item['type'] == {'d4': 'model_anomaly', 'collision': 'collision',
                                                    'forbidden-zone': 'forbidden_zone'}[args.scenario])
                fixture_operator = next(profile["operator_id"] for profile in backend.operator_profiles()
                                        if profile["sector_id"] == incident["responsible_sector_id"])
                report["fixture_operator"] = fixture_operator
                if fixture_operator != "dispatcher-1":
                    # Assign only this temporary fixture account to the actual recipient.
                    administrator = next(user for user in app.state.auth.users() if user["role"] == "admin")
                    app.state.auth.update_user(login.json()["user"]["id"], {"operator_id": fixture_operator}, administrator)
                    login = client.post("/api/auth/login", json={"username": "live.acceptance", "password": password})
                    login.raise_for_status()
                    headers = {"X-CSRF-Token": login.json()["csrf_token"],
                               "X-Expected-User": login.json()["user"]["id"]}
                if args.scenario == 'forbidden-zone':
                    # Explicit historical fixture in the disposable test database only.
                    historical = dict(incident, incident_id='fixture-previous-zone', status='closed',
                                      detected_at=stamp(clock_now-timedelta(days=1)))
                    with backend.store.transaction() as db:
                        db.execute('INSERT INTO incidents VALUES (?,?,?)',
                                   (historical['incident_id'], 'fixture-previous-zone', json.dumps(historical)))
                    for action in ({'action':'claim'}, {'action':'record_response','response_code':'checked',
                                    'reason':'Связался с водителем: сообщил о погрузке.'}):
                        response=client.patch(f"/api/incidents/{incident['incident_id']}", headers=headers,
                            json={**action,'expected_revision':incident['dispatch_revision'],
                                  'request_id':secrets.token_hex(12)})
                        response.raise_for_status();incident=response.json()
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
                if job['stale']:
                    raise RuntimeError('Первый анализ уже устарел до тестового изменения данных')
                presentation = job['result'].get('presentation', {})
                if args.scenario == 'forbidden-zone':
                    if 'похожих случаев: 1' not in presentation.get('history_summary','') or not presentation.get('dispatcher_notes'):
                        raise RuntimeError('История или сохранённая заметка не попали в резюме')
                    report['history_and_dispatcher_note_checked']=True
                readable = json.dumps(presentation, ensure_ascii=False)
                if presentation.get('version') != 2 or presentation.get('entity') != 'Погрузчик 1':
                    raise RuntimeError('Нет понятного описания с зарегистрированным названием объекта')
                if 'payload.' in readable or any(fact['id'] in readable for fact in job['result']['facts']):
                    raise RuntimeError('Технические поля попали в основной текст')
                if job['result']['technical']['snapshot']['as_of'] != presentation.get('as_of'):
                    raise RuntimeError('Время отображения не совпадает с сохранённым срезом')
                report['readable_presentation_checked'] = True
                visible = ' '.join([presentation[key] for key in ('title', 'description', 'established', 'attention', 'unknown')]
                                   + [presentation['state']['text']] + presentation['observations'] + presentation['recommendations'])
                if re.search(r'MLP|Qwen|JSON|payload|порог|оценк|модель|алгоритм|\d+[.,]\d+|≈', visible, re.I):
                    raise RuntimeError('Технический термин или числовая оценка попали в основной текст')
                if not job['result']['technical'].get('semantic_check'):
                    raise RuntimeError('Нет отдельной проверки смысла свободного текста модели')
                if args.scenario == "d4":
                    verified = {fact["field"] for fact in job["result"]["facts"]
                                if fact["source"] == "model_observation" and fact["id"] == observation["observation_id"]}
                    if not {"status", "score", "threshold"} <= verified:
                        raise RuntimeError("В отчёте нет обязательных фактов MLP")
                    if presentation['title'] != 'Обнаружено необычное движение' or 'Наличие аварии не установлено' not in visible:
                        raise RuntimeError('Некорректное объяснение модельного подозрения')
                    measured = {fact['field']: fact['value'] for fact in job['result']['technical']['verified_facts']
                                if fact['source'] == 'model_observation' and fact['id'] == observation['observation_id']}
                    if measured.get('score') != observation['score'] or measured.get('threshold') != observation['threshold']:
                        raise RuntimeError('Не сохранены точные подтверждённые показатели в техническом разделе')
                    snapshot = job['result']['technical']['snapshot']
                    position = max((row for row in snapshot['events'] if row['type'] == 'position'
                                    and row['payload'].get('asset_id') == 'V1'), key=lambda row: row['event_time'])
                    area = next(row for row in backend.site['site_areas'] if row['id'] == snapshot['incident']['site_area_id'])
                    rect = area.get('rectangle')
                    report['location_check'] = {'site_area_id': area['id'], 'name': area['name'],
                        'position_event_id': position['event_id'], 'position': position['payload'],
                        'area_rectangle': rect, 'inside_area_rectangle': bool(rect and backend._inside(
                            position['payload']['x'], position['payload']['y'], rect)),
                        'meaning': 'registered map area, not confirmation of being inside a building'}
                elif args.scenario == 'collision' and not {event["event_id"] for event in events} <= set(job["result"]["evidence_event_ids"]):
                    raise RuntimeError("В отчёте нет доказательств обеих машин")
                elif args.scenario == 'forbidden-zone':
                    if presentation.get('place') != zone['name'] or presentation['state']['confirmed_exit']:
                        raise RuntimeError('Некорректное название зоны или выдуманный выход из неё')
                    if not job['result']['evidence_event_ids']:
                        raise RuntimeError('Нет проверенных событий зоны')
                    clock_now += timedelta(seconds=1)
                    changed = {**events[0], 'event_id': 'live-zone-changed-position',
                               'event_time': stamp(clock_now - timedelta(milliseconds=100)),
                               'payload': {**events[0]['payload'], 'x': events[0]['payload']['x'] + 0.5}}
                    response = client.post('/api/events', json=changed,
                                           headers={'X-Source-Key': os.environ['DISPATCH_SOURCE_KEY']})
                    response.raise_for_status()
                    response = client.get(f'/api/agent-jobs/{job_id}', headers={'X-Expected-User': headers['X-Expected-User']})
                    response.raise_for_status()
                    stale_job = response.json()
                    if not stale_job['stale'] or stale_job['result']['presentation'] != presentation:
                        raise RuntimeError('Изменение данных не пометило прежний срез как устаревший')
                    report.update(stale_job=stale_job, changed_event=changed,
                                  stale_after_actual_change_checked=True)
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
