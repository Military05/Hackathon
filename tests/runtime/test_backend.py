"""Acceptance of live FastAPI routes and real SQLite, with controllable server time."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from src.core.geometry import contains_point
from src.core.main import create_app
from src.core.service import stamp


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class BackendAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "dispatch.db"
        self.clock = Clock()
        self.app = create_app(db_path=self.db_path, enable_scheduler=False, clock=self.clock)
        self.service = self.app.state.service
        self.client = TestClient(self.app)
        self.counter = 0

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def position(self, x=20, y=50, vehicle="V1", seconds_ago=0, event_id=None):
        self.counter += 1
        body = {"event_id": event_id or f"event-{self.counter}", "event_time": stamp(self.clock() - timedelta(seconds=seconds_ago)),
                "sensor_id": "POS-" + vehicle, "type": "position", "demo": True,
                "payload": {"asset_id": vehicle, "x": x, "y": y}}
        response = self.client.post("/api/events", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return body

    def access(self):
        self.counter += 1
        body = {"event_id": f"access-{self.counter}", "event_time": stamp(self.clock()),
                "sensor_id": "ACCESS-O1", "type": "access", "demo": True,
                "payload": {"employee_id": "U1", "building_id": "O1", "direction": "in"}}
        self.assertEqual(self.client.post("/api/events", json=body).status_code, 201)
        return self.client.get("/api/incidents").json()[-1]

    def patch(self, incident, action, operator="dispatcher-1", **extra):
        revision = incident["dispatch_revision"] if isinstance(incident, dict) else self.service.get_incident(incident)["dispatch_revision"]
        iid = incident["incident_id"] if isinstance(incident, dict) else incident
        self.counter += 1
        return self.client.patch("/api/incidents/" + iid, headers={"X-Demo-Operator": operator},
                                 json={"action": action, "expected_revision": revision, "request_id": f"request-{self.counter}", **extra})

    def ready(self, operator, session="tab-1", availability="ready"):
        return self.client.post("/api/operator-presence", headers={"X-Demo-Operator": operator},
                                json={"session_id": session, "availability": availability})

    def zone(self):
        self.position()
        return self.client.get("/api/incidents").json()[0]

    def test_contract_geometry_and_no_gps(self):
        site = self.client.get("/api/site").json()
        self.assertEqual(site["coordinate_system"]["gps"], False)
        self.assertTrue({"W1", "W2", "P1", "P2", "O1", "G1"} <= {b["id"] for b in site["buildings"]})
        self.assertGreaterEqual(len(site["buildings"]), 14)
        self.assertTrue(contains_point(0, 0, [(0, 0), (10, 0), (10, 10), (0, 10)]))
        self.assertFalse(contains_point(11, 5, [(0, 0), (10, 0), (10, 10), (0, 10)]))
        self.assertEqual(self.client.get("/api/health").json()["contract_version"], 2)

    def test_event_idempotency_and_conflicting_body(self):
        event = self.position()
        response = self.client.post("/api/events", json=event)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["duplicate"])
        event["payload"]["x"] = 25
        self.assertEqual(self.client.post("/api/events", json=event).status_code, 409)
        self.assertEqual(len(self.client.get("/api/events").json()), 1)
        self.assertEqual(len(self.client.get("/api/incidents").json()), 1)

    def test_normalized_coordinate_duplicate(self):
        event = self.position(x=20, y=50)
        event["payload"]["x"] = 20.0
        event["payload"]["y"] = 50.0
        self.assertEqual(self.client.post("/api/events", json=event).status_code, 200)

    def test_event_validation_and_history_errors(self):
        event = self.position(x=39, y=85)
        for x in (-1, 101, True):
            changed = {**event, "event_id": "invalid-" + str(x), "payload": {"asset_id": "V1", "x": x, "y": 50}}
            self.assertEqual(self.client.post("/api/events", json=changed).status_code, 422)
        future = {**event, "event_id": "future", "event_time": stamp(self.clock() + timedelta(seconds=2.1))}
        self.assertEqual(self.client.post("/api/events", json=future).json()["code"], "future_event")
        future["event_time"] = stamp(self.clock() + timedelta(seconds=2))
        self.assertEqual(self.client.post("/api/events", json=future).status_code, 201)
        self.assertEqual(self.client.get("/api/events?asset_id=missing").status_code, 404)
        self.assertEqual(self.client.get("/api/events?since=bad").status_code, 422)
        self.assertEqual(self.client.get("/api/events?limit=501").status_code, 422)

    def test_zone_boundary_hysteresis_and_new_episode(self):
        incident = self.zone()
        self.position(x=12, y=43)  # Included boundary.
        self.assertEqual(len(self.service.list_incidents()), 1)
        self.clock.advance(0.5)
        self.position(x=39, y=58)
        self.assertTrue(self.service.get_incident(incident["incident_id"])["condition_active"])
        self.clock.advance(0.5)
        self.position(x=39, y=59)
        self.assertFalse(self.service.get_incident(incident["incident_id"])["condition_active"])
        self.clock.advance(0.5)
        self.position()
        self.assertEqual(len(self.service.list_incidents()), 2)

    def test_old_measurement_preserved_without_rolling_back_position(self):
        event = self.position(x=39, y=85)
        self.position(x=20, y=50, seconds_ago=20)
        asset = next(a for a in self.client.get("/api/assets").json() if a["id"] == "V1")
        self.assertEqual(asset["event_id"], event["event_id"])
        self.assertEqual(asset["x"], 39)
        self.assertEqual(self.service.list_incidents(), [])
        self.assertEqual(len(self.service.event_history("V1")), 2)

    def test_stale_unknown_prevents_close_and_resets_exit_counter(self):
        incident = self.zone()
        claimed = self.patch(incident, "claim").json()
        self.clock.advance(0.5)
        self.position(x=39, y=58)
        self.clock.advance(5)
        self.service.tick()
        current = self.service.get_incident(incident["incident_id"])
        self.assertEqual(current["condition_state"], "unknown")
        rejected = self.patch(current, "close", reason="Проверено")
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()["code"], "condition_unknown")
        self.position(x=39, y=58)
        self.assertTrue(self.service.get_incident(incident["incident_id"])["condition_active"])
        self.clock.advance(0.5)
        self.position(x=39, y=59)
        restored = self.service.get_incident(incident["incident_id"])
        self.assertFalse(restored["condition_active"])
        self.assertEqual(self.patch(restored, "close", reason="Проверено").status_code, 200)

    def test_concurrent_claim_exactly_one_winner_and_persistent_owner(self):
        incident = self.zone()
        def claim(operator):
            return self.client.patch("/api/incidents/" + incident["incident_id"], headers={"X-Demo-Operator": operator},
                json={"action": "claim", "expected_revision": 0, "request_id": "concurrent-" + operator})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(claim, ["dispatcher-1", "dispatcher-3"]))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        winner = next(r.json() for r in responses if r.status_code == 200)
        restarted = create_app(db_path=self.db_path, enable_scheduler=False, clock=self.clock)
        self.assertEqual(restarted.state.service.get_incident(incident["incident_id"])["assigned_operator_id"], winner["assigned_operator_id"])
        claims = [h for h in self.service.get_incident(incident["incident_id"])["history"] if h["action"] == "claim"]
        self.assertEqual(len(claims), 1)

    def test_patch_idempotency_is_checked_before_stale_revision(self):
        incident = self.zone()
        body = {"action": "claim", "expected_revision": 0, "request_id": "same-request"}
        url = "/api/incidents/" + incident["incident_id"]
        headers = {"X-Demo-Operator": "dispatcher-1"}
        first = self.client.patch(url, headers=headers, json=body)
        second = self.client.patch(url, headers=headers, json=body)
        self.assertEqual(first.json(), second.json())
        changed = self.client.patch(url, headers=headers, json={**body, "expected_revision": 1})
        self.assertEqual(changed.json()["code"], "request_conflict")
        self.assertEqual(self.client.patch(url, headers=headers, json={"status": "closed"}).status_code, 422)

    def test_transfer_keeps_owner_until_accept_and_records_once(self):
        incident = self.zone()
        self.ready("dispatcher-2")
        claimed = self.patch(incident, "claim").json()
        pending = self.patch(claimed, "request_transfer", to_operator_id="dispatcher-2", reason="Нужна помощь").json()
        self.assertEqual(pending["assigned_operator_id"], "dispatcher-1")
        transfer = pending["pending_transfer"]
        accepted = self.patch(pending, "accept_transfer", operator="dispatcher-2", transfer_id=transfer["transfer_id"])
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["assigned_operator_id"], "dispatcher-2")
        self.assertIsNone(accepted.json()["pending_transfer"])
        self.assertEqual(len([h for h in accepted.json()["history"] if h["action"] == "accept_transfer"]), 1)

    def test_transfer_timeout_keeps_owner_and_no_extra_logistics_incident(self):
        incident = self.zone()
        self.ready("dispatcher-2")
        claimed = self.patch(incident, "claim").json()
        pending = self.patch(claimed, "request_transfer", to_operator_id="dispatcher-2", reason="Помощь").json()
        self.clock.advance(20)
        self.service.tick()
        current = self.service.get_incident(incident["incident_id"])
        self.assertEqual(current["assigned_operator_id"], "dispatcher-1")
        self.assertIsNone(current["pending_transfer"])
        self.assertEqual(len([i for i in self.service.list_incidents() if i["type"] == "forbidden_zone"]), 1)
        notifications = self.service.notifications("dispatcher-1")["notifications"]
        self.assertEqual(len([n for n in notifications if n["kind"] == "transfer_expired"]), 1)
        self.service.tick()
        self.assertEqual(len(self.service.notifications("dispatcher-1")["notifications"]), len(notifications))

    def test_unknown_expected_sensor_never_started_persists_then_recovers(self):
        self.assertEqual(self.service.sensor_health("HB-QA")["status"], "unknown")
        self.clock.advance(10)
        self.service.tick()
        incident = next(i for i in self.service.list_incidents() if i.get("sensor_id") == "HB-QA")
        self.assertEqual(incident["details"]["cause"], "never_started")
        self.assertEqual(incident["evidence_event_ids"], [])
        restarted = create_app(db_path=self.db_path, enable_scheduler=False, clock=self.clock)
        restarted.state.service.tick()
        self.assertEqual(len([i for i in restarted.state.service.list_incidents() if i.get("sensor_id") == "HB-QA"]), 1)
        body = {"event_id": "real-first-heartbeat", "event_time": stamp(self.clock()), "sensor_id": "HB-QA", "type": "heartbeat", "demo": True, "payload": {}}
        self.assertEqual(self.client.post("/api/events", json=body).status_code, 201)
        self.assertEqual(self.service.get_incident(incident["incident_id"])["condition_state"], "restored")

    def test_presence_multiple_tabs_and_recovery_reserve(self):
        self.ready("dispatcher-1", "a")
        self.ready("dispatcher-1", "b")
        response = self.ready("dispatcher-1", "a", "away")
        self.assertTrue(response.json()["operator_ready"])
        incident = self.zone()
        claimed = self.patch(incident, "claim").json()
        self.ready("dispatcher-1", "b", "away")
        self.service.tick()
        self.clock.advance(15)
        self.ready("dispatcher-3")
        self.ready("dispatcher-2")
        current = self.service.get_incident(incident["incident_id"])
        denied = self.patch(current, "reassign_unavailable", operator="dispatcher-2", to_operator_id="dispatcher-2", reason="Восстановление")
        self.assertEqual(denied.status_code, 409)
        recovered = self.patch(current, "reassign_unavailable", operator="dispatcher-3", to_operator_id="dispatcher-2", reason="Восстановление")
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["assigned_operator_id"], "dispatcher-2")

    def test_notifications_cursor_does_not_skip_pages_and_survives_restart(self):
        for _ in range(4):
            self.access()
        first = self.service.notifications("dispatcher-3", 0, 2)
        second = self.service.notifications("dispatcher-3", first["next_seq"], 2)
        self.assertEqual(len(first["notifications"]), 2)
        self.assertEqual(len(second["notifications"]), 2)
        self.assertGreater(second["next_seq"], first["next_seq"])
        restarted = create_app(db_path=self.db_path, enable_scheduler=False, clock=self.clock)
        self.assertEqual(restarted.state.service.notifications("dispatcher-3", first["next_seq"], 2), second)

    def test_response_and_dismiss_do_not_hide_precise_active_condition(self):
        incident = self.zone()
        claimed = self.patch(incident, "claim").json()
        response = self.patch(claimed, "record_response", response_code="contacted", reason="Связались со службой")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["condition_active"])
        self.assertEqual(len(response.json()["response_history"]), 1)
        rejected = self.patch(response.json(), "dismiss_model", reason="Сигнал не подтверждён")
        self.assertEqual(rejected.status_code, 409)
        self.assertTrue(self.service.get_incident(incident["incident_id"])["condition_active"])

    def test_model_episode_dismissed_raw_kept_and_two_normal_windows_restore(self):
        event = self.position(x=39, y=85)
        def observation(number, status):
            return {"observation_id": "observation-" + str(number), "asset_id": "V1",
                    "window_start": stamp(self.clock() - timedelta(seconds=10)), "window_end": stamp(self.clock()),
                    "status": status, "score": 0.9 if status == "anomaly" else 0.1,
                    "threshold": 0.5, "model_version": "test-model", "feature_version": "movement-v1",
                    "evidence_event_ids": [event["event_id"]], "demo": True}
        self.service.register_model_observation(observation(1, "anomaly"))
        incident = self.service.list_incidents()[0]
        claimed = self.patch(incident, "claim", operator="dispatcher-3").json()
        dismissed = self.patch(claimed, "dismiss_model", operator="dispatcher-3", reason="Проверено").json()
        self.assertTrue(dismissed["condition_active"])
        self.assertEqual(dismissed["disposition"], "rejected_model_signal")
        self.service.register_model_observation(observation(2, "anomaly"))
        self.assertEqual(len(self.service.list_incidents()), 1)
        self.assertEqual(sum(i["active_count"] for i in self.service.summary()["sectors"]), 0)
        self.service.register_model_observation(observation(3, "normal"))
        self.assertTrue(self.service.get_incident(incident["incident_id"])["condition_active"])
        self.service.register_model_observation(observation(4, "normal"))
        self.assertFalse(self.service.get_incident(incident["incident_id"])["condition_active"])
        self.service.register_model_observation(observation(5, "anomaly"))
        self.assertEqual(len(self.service.list_incidents()), 2)
        self.assertEqual(len(self.service.model_observations()), 5)

    def test_workspace_authorization_and_unknown_position_routing(self):
        incident = self.zone()
        self.assertEqual(self.client.get("/api/incidents?scope=workstation").status_code, 422)
        denied = self.patch(incident, "claim", operator="dispatcher-2")
        self.assertEqual(denied.json()["code"], "operator_conflict")
        checkpoint = next(area["rectangle"] for area in self.service.site["site_areas"] if area["id"] == "checkpoint")
        self.position(x=checkpoint["x"] + checkpoint["width"] / 2,
                      y=checkpoint["y"] + checkpoint["height"] / 2, vehicle="V2")
        self.assertEqual(next(a for a in self.service.list_assets() if a["id"] == "V2")["site_area_id"], "checkpoint")
        self.position(x=99, y=99, vehicle="V3")
        self.assertEqual(next(a for a in self.service.list_assets() if a["id"] == "V3")["site_area_id"], "unknown")


if __name__ == "__main__":
    unittest.main()
