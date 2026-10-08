"""Detector timing and atomic dispatch regression checks with real SQLite."""
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.core.service import ApiError, Service, stamp

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class BackendRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.path = Path(self.temp.name) / "dispatch.sqlite"
        self.service = self.restart()
        self.counter = 0

    def restart(self):
        return Service(self.path, ROOT / "data/demo/site.json", clock=self.clock)

    def position(self, x=25, y=50, source_age=0, event_id=None):
        self.counter += 1
        event = {"event_id": event_id or f"position-{self.counter}",
                 "event_time": stamp(self.clock() - timedelta(seconds=source_age)),
                 "sensor_id": "POS-V1", "type": "position", "demo": True,
                 "payload": {"asset_id": "V1", "x": x, "y": y}}
        self.service.ingest_event(event)
        return event

    def incident(self):
        self.position()
        return next(i for i in self.service.list_incidents() if i["type"] == "forbidden_zone")

    def ready(self, operator, availability="ready", session="tab"):
        return self.service.operator_presence(operator, {"session_id": session, "availability": availability})

    def action(self, incident, action, operator="dispatcher-1", **extra):
        self.counter += 1
        return self.service.action(incident["incident_id"], operator,
                                   {"action": action, "expected_revision": incident["dispatch_revision"],
                                    "request_id": f"request-{self.counter}", **extra})

    def absence(self, operator="dispatcher-1"):
        with self.service.store.read() as db:
            return json.loads(db.execute("SELECT value FROM metadata WHERE key=?", ("absence:" + operator,)).fetchone()[0])

    def reject_reserve_assignment(self, incident, recipient="dispatcher-3"):
        before = self.service.get_incident(incident["incident_id"])
        with self.assertRaises(ApiError) as error:
            self.action(incident, "reassign_unavailable", operator="dispatcher-3",
                        to_operator_id=recipient, reason="Владелец недоступен")
        self.assertEqual((error.exception.status, error.exception.code), (422, "invalid_action"))
        self.assertEqual(self.service.get_incident(incident["incident_id"]), before)

    def test_stale_threshold_point_never_triggers_zone(self):
        self.position(source_age=5)
        self.assertEqual(self.service.list_incidents(), [])
        asset = next(a for a in self.service.list_assets() if a["asset_id"] == "V1")
        self.assertEqual(asset["position_state"], "unknown")

    def test_stale_heartbeat_does_not_restore_offline_condition(self):
        self.clock.advance(10)
        self.service.tick()
        item = next(i for i in self.service.list_incidents() if i.get("sensor_id") == "HB-QA")
        event = {"event_id": "delayed-heartbeat", "event_time": stamp(self.clock() - timedelta(seconds=5)),
                 "sensor_id": "HB-QA", "type": "heartbeat", "demo": True, "payload": {}}
        self.service.ingest_event(event)
        self.assertEqual(self.service.sensor_health("HB-QA")["status"], "online")
        self.assertTrue(self.service.get_incident(item["incident_id"])["condition_active"])
        self.service.ingest_event({**event, "event_id": "fresh-heartbeat", "event_time": stamp(self.clock())})
        self.assertFalse(self.service.get_incident(item["incident_id"])["condition_active"])

    def test_exit_counter_resets_at_exact_stale_gap_without_timer(self):
        item = self.incident()
        self.clock.advance(0.5)
        self.position(39, 58)
        self.clock.advance(5)
        self.position(39, 58)
        self.assertTrue(self.service.get_incident(item["incident_id"])["condition_active"])
        self.clock.advance(0.5)
        self.position(39, 58)
        self.assertFalse(self.service.get_incident(item["incident_id"])["condition_active"])

    def test_delayed_fresh_delivery_breaks_exit_sequence_after_observation_gap(self):
        item = self.incident()
        self.clock.advance(0.5)
        self.position(39, 58)
        self.clock.advance(6)
        self.position(39, 58, source_age=1.1)
        self.assertTrue(self.service.get_incident(item["incident_id"])["condition_active"])
        self.clock.advance(0.5)
        self.position(39, 58)
        self.assertFalse(self.service.get_incident(item["incident_id"])["condition_active"])

    def test_away_transition_records_absence_without_assigning_reserve(self):
        self.ready("dispatcher-1")
        claimed = self.action(self.incident(), "claim")
        self.ready("dispatcher-1", "away")
        since = stamp(self.clock())
        self.clock.advance(15)
        self.ready("dispatcher-3")
        self.assertEqual(self.absence()["since"], since)
        self.reject_reserve_assignment(claimed)
        self.assertFalse(next(p for p in self.service.operator_profiles() if p["operator_id"] == "dispatcher-1")["operator_ready"])
    def test_away_heartbeat_preserves_expired_lease_without_reserve(self):
        self.ready("dispatcher-1")
        claimed = self.action(self.incident(), "claim")
        deadline = stamp(self.clock() + timedelta(seconds=self.service.config["presence_timeout_seconds"]))
        self.clock.advance(30)
        self.ready("dispatcher-1", "away")
        episode = self.absence()
        self.assertEqual(episode["since"], deadline)
        self.clock.advance(5)
        self.ready("dispatcher-1", "away")
        self.assertEqual(self.absence(), episode)
        self.reject_reserve_assignment(claimed)
    def test_restart_preserves_expired_absence_and_requires_renewed_presence(self):
        self.ready("dispatcher-1")
        claimed = self.action(self.incident(), "claim")
        deadline = stamp(self.clock() + timedelta(seconds=self.service.config["presence_timeout_seconds"]))
        self.clock.advance(31)
        self.service = self.restart()
        episode = self.absence()
        self.assertEqual(episode["since"], deadline)
        self.clock.advance(5)
        self.service = self.restart()
        self.assertEqual(self.absence(), episode)
        self.reject_reserve_assignment(claimed)
        self.assertTrue(self.ready("dispatcher-1")["operator_ready"])
        self.assertIsNone(self.absence())
    def test_ready_tab_keeps_operator_ready_when_another_tab_goes_away(self):
        self.ready("dispatcher-1", session="a")
        self.ready("dispatcher-1", session="b")
        claimed = self.action(self.incident(), "claim")
        self.ready("dispatcher-1", "away", "a")
        self.clock.advance(14)
        self.ready("dispatcher-1", session="b")
        self.clock.advance(1)
        self.ready("dispatcher-3")
        profile = next(p for p in self.service.operator_profiles() if p["operator_id"] == "dispatcher-1")
        self.assertTrue(profile["operator_ready"])
        self.assertIsNone(self.absence())
        self.reject_reserve_assignment(claimed)
    def test_failure_after_claim_rolls_back_owner_history_and_idempotency(self):
        item = self.incident()
        request = {"action": "claim", "expected_revision": 0, "request_id": "retryable-claim"}
        with patch.object(self.service, "_history", side_effect=RuntimeError("storage fault")):
            with self.assertRaises(RuntimeError):
                self.service.action(item["incident_id"], "dispatcher-1", request)
        current = self.service.get_incident(item["incident_id"])
        self.assertIsNone(current["assigned_operator_id"])
        self.assertEqual(current["dispatch_revision"], 0)
        self.assertFalse(any(h["action"] == "claim" for h in current["history"]))
        accepted = self.service.action(item["incident_id"], "dispatcher-1", request)
        repeated = self.service.action(item["incident_id"], "dispatcher-1", request)
        self.assertEqual(accepted, repeated)
        self.assertEqual(sum(h["action"] == "claim" for h in repeated["history"]), 1)

    def test_independent_sqlite_connections_have_one_claim_winner(self):
        item = self.incident()
        second = self.restart()

        def claim(pair):
            service, request_id = pair
            try:
                service.action(item["incident_id"], "dispatcher-1",
                               {"action": "claim", "expected_revision": 0, "request_id": request_id})
                return 200
            except ApiError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(claim, [(self.service, "claim-a"), (second, "claim-b")]))
        self.assertEqual(sorted(result), [200, 409])
        current = self.service.get_incident(item["incident_id"])
        self.assertEqual(sum(h["action"] == "claim" for h in current["history"]), 1)

    def test_rejected_reserve_action_keeps_addressed_transfer_acceptance_atomic(self):
        self.ready("dispatcher-1")
        self.ready("dispatcher-2")
        claimed = self.action(self.incident(), "claim")
        pending = self.action(claimed, "request_transfer", to_operator_id="dispatcher-2", reason="Передача участка")
        transfer_id = pending["pending_transfer"]["transfer_id"]
        self.ready("dispatcher-1", "away")
        self.clock.advance(5)
        self.reject_reserve_assignment(pending, "dispatcher-2")
        current = self.service.get_incident(pending["incident_id"])
        self.assertEqual(current["pending_transfer"]["transfer_id"], transfer_id)
        accepted = self.action(current, "accept_transfer", operator="dispatcher-2", transfer_id=transfer_id)
        self.assertIsNone(accepted["pending_transfer"])
        self.assertEqual(accepted["assigned_operator_id"], "dispatcher-2")
        self.assertEqual(sum(h["action"] == "accept_transfer" for h in accepted["history"]), 1)
        with self.assertRaises(ApiError) as error:
            self.action(accepted, "accept_transfer", operator="dispatcher-2", transfer_id=transfer_id)
        self.assertEqual(error.exception.code, "transfer_conflict")
    def test_cursor_validation_and_pagination_never_skip_records(self):
        for number in range(5):
            self.service.ingest_event({"event_id": f"access-{number}", "event_time": stamp(self.clock()),
                                      "sensor_id": "ACCESS-O1", "type": "access", "demo": True,
                                      "payload": {"employee_id": "U1", "building_id": "O1", "direction": "in"}})
        pages, cursor = [], 0
        while True:
            page = self.service.notifications("dispatcher-3", cursor, 2)
            pages.extend(page["notifications"])
            cursor = page["next_seq"]
            if not page["notifications"]:
                break
        self.assertEqual(len(pages), 5)
        self.assertEqual(len({n["seq"] for n in pages}), 5)
        for bad in (True, "0", None, -1):
            with self.assertRaises(ApiError) as error:
                self.service.notifications("dispatcher-3", after_seq=bad)
            self.assertEqual(error.exception.code, "invalid_cursor")


if __name__ == "__main__":
    unittest.main()
