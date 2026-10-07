"""Confirmed checkpoint journal, persisted shift correlation and safe CSV over SQLite."""
import asyncio
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.core.demo_traffic import DemoRunner
from src.core.operations import Operations, csv_bytes
from src.core.service import ApiError, Service, stamp


ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds=1):
        self.now += timedelta(seconds=seconds)


def checkpoint_site(path):
    site = json.loads((ROOT / "data/demo/site.json").read_text(encoding="utf-8"))
    if not any(sensor["id"] == "ACCESS-G1" for sensor in site["sensors"]):
        site["sensors"].append({"id": "ACCESS-G1", "type": "access", "building_id": "G1", "site_area_id": "checkpoint"})
    if not any(asset["id"] == "U4" for asset in site["assets"]):
        site["assets"].append({"id": "U4", "type": "employee", "name": "Гость без допуска"})
        site["permissions"].append({"employee_id": "U4", "allowed_zone_ids": [], "allowed_building_ids": []})
    path.write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
    return path


class OperationsAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.site_path = checkpoint_site(Path(self.temp.name) / "site.json")
        self.db_path = Path(self.temp.name) / "checkpoint.db"
        self.service = Service(self.db_path, self.site_path, clock=self.clock)
        self.operations = Operations(self.service)
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def access(self, employee="U1", direction="in", shift_id=None, event_id=None):
        self.sequence += 1
        event = {"event_id": event_id or "gate-" + str(self.sequence), "event_time": stamp(self.clock()),
                 "sensor_id": "ACCESS-G1", "type": "access", "demo": True,
                 "payload": {"employee_id": employee, "building_id": "G1", "direction": direction}}
        result = self.service.ingest_event(event, shift_id=shift_id)
        return event, result

    def action(self, item, action, operator="dispatcher-3", **fields):
        self.sequence += 1
        return self.service.action(item["incident_id"], operator, {"action": action, "expected_revision": item["dispatch_revision"],
                                                                  "request_id": "op-" + str(self.sequence), **fields})

    def test_allowed_passage_is_journaled_without_incident_and_duplicate_is_one_row(self):
        event, reply = self.access()
        self.assertFalse(reply["duplicate"])
        self.assertTrue(self.service.ingest_event(event)["duplicate"])
        self.assertEqual(self.service.list_incidents(), [])
        result = self.operations.journal()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["permission"], "allowed")
        self.assertEqual(result["occupancy"]["observed_inside_count"], 1)
        self.assertFalse(result["occupancy"]["initial_state_known"])
        self.assertTrue(result["occupancy"]["gate_source_online"])

    def test_unauthorized_actual_ingress_is_one_violation_and_out_is_regular(self):
        event, _ = self.access("U4")
        self.service.ingest_event(event)
        self.clock.advance()
        self.access("U4", "out")
        items = self.operations.journal()["items"]
        self.assertEqual([(item["direction"], item["permission"]) for item in items], [("out", "allowed"), ("in", "violation")])
        self.assertEqual(len(self.service.list_incidents()), 1)
        self.assertEqual(self.service.list_incidents()[0]["building_id"], "G1")
        self.assertEqual(self.operations.journal()["occupancy"]["observed_inside_count"], 0)

    def test_latest_pagination_search_literal_wildcard_filters_and_time_range(self):
        self.access("U1")
        first = stamp(self.clock())
        self.clock.advance()
        self.access("U2")
        self.clock.advance()
        self.access("U4")
        self.clock.advance()
        self.access("U1", "out")
        last = stamp(self.clock())
        self.assertEqual([item["employee_id"] for item in self.operations.journal(limit=2, offset=1)["items"]], ["U4", "U2"])
        self.assertEqual(self.operations.journal(q="U1")["total"], 2)
        self.assertEqual(self.operations.journal(q="Гость")["total"], 1)
        self.assertEqual(self.operations.journal(q="%_")["total"], 0)
        self.assertEqual(self.operations.journal(direction="out")["total"], 1)
        self.assertEqual(self.operations.journal(permission="violation")["total"], 1)
        self.assertEqual(self.operations.journal(since=first, until=last)["total"], 4)
        self.assertEqual(self.operations.journal(since=last)["total"], 1)
        # A view filter does not pretend to change total observed site occupancy.
        self.assertEqual(self.operations.journal(q="not-found")["occupancy"]["observed_inside_count"], 2)

    def test_invalid_bounds_and_filters_fail_before_read(self):
        for values in ({"limit": 201}, {"limit": True}, {"offset": -1}, {"offset": 100001}, {"q": "x" * 101},
                       {"direction": "enter"}, {"permission": "denied"}, {"since": "bad"},
                       {"since": "2026-10-08T00:00:00Z", "until": "2026-10-07T00:00:00Z"}):
            with self.assertRaises(ApiError, msg=values):
                self.operations.journal(**values)

    def test_csv_bom_formula_escape_and_confirmed_employee_export(self):
        payload = csv_bytes(["text"], [{"text": value} for value in ("=1+2", " +CMD", "-1", "@SUM(1)", "\tformula", "Обычный")])
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertTrue(all(row["text"].startswith("'") for row in rows[:5]))
        self.assertEqual(rows[-1]["text"], "Обычный")
        self.access("U2")
        result = list(csv.DictReader(io.StringIO(self.operations.checkpoint_csv(q="U2").decode("utf-8-sig"))))
        self.assertEqual(result[0]["employee_id"], "U2")

    def test_shift_correlation_is_atomic_idempotent_and_survives_restart(self):
        shift_id = self.operations.start_shift(["U1", "U2", "U3"])
        first = None
        for employee in ("U1", "U2", "U3"):
            event, _ = self.access(employee, shift_id=shift_id)
            first = first or event
        self.service.ingest_event(first, shift_id=shift_id)
        self.assertTrue(self.operations.release_transport(shift_id))
        result = self.operations.shift(shift_id)
        self.assertEqual(result["gate_count"], 3)
        self.assertEqual(result["gate_event_count"], 3)
        self.assertEqual(result["phase"], "transport")
        self.assertEqual({item["shift_id"] for item in self.operations.journal()["items"]}, {shift_id})
        restarted = Service(self.db_path, self.site_path, clock=self.clock)
        restored = Operations(restarted)
        self.assertEqual(restored.current_shift()["phase"], "interrupted")
        self.assertEqual(restored.current_shift()["gate_count"], 3)
        self.assertTrue(restarted.ingest_event(first, shift_id=shift_id)["duplicate"])
        self.assertEqual(restored.current_shift()["gate_event_count"], 3)
        self.assertEqual(restored.journal()["items"][0]["shift_id"], shift_id)
        self.assertEqual(Operations(restarted).current_shift()["phase"], "interrupted")

    def test_incomplete_shift_cannot_release_and_bad_correlation_rolls_back_event(self):
        shift_id = self.operations.start_shift(["U1", "U2"])
        self.access("U1", shift_id=shift_id)
        self.assertFalse(self.operations.release_transport(shift_id))
        before = self.operations.journal()["total"]
        with self.assertRaises(ApiError):
            self.access("U2", shift_id="unknown")
        self.assertEqual(self.operations.journal()["total"], before)
        self.operations.finish_shift(shift_id)
        self.assertEqual(self.operations.shift(shift_id)["phase"], "stopped")

    def test_shift_replay_cannot_attach_an_event_to_another_shift(self):
        first_id = self.operations.start_shift(["U1"])
        event, _ = self.access("U1", shift_id=first_id)
        second_id = self.operations.start_shift(["U1"])
        with self.assertRaisesRegex(ApiError, "another shift"):
            self.service.ingest_event(event, shift_id=second_id)
        self.assertEqual(self.operations.shift(second_id)["gate_count"], 0)

    def test_dispatch_activity_and_export_count_real_successful_actions_only(self):
        self.access("U4")
        item = self.service.list_incidents()[0]
        for operator in ("dispatcher-3", "dispatcher-1"):
            self.service.operator_presence(operator, {"session_id": "tab", "availability": "ready"})
        item = self.action(item, "claim")
        item = self.action(item, "record_response", reason="Связались с КПП", response_code="contacted")
        item = self.action(item, "request_transfer", to_operator_id="dispatcher-1", reason="=Передача")
        item = self.action(item, "accept_transfer", operator="dispatcher-1", transfer_id=item["pending_transfer"]["transfer_id"])
        item = self.action(item, "close", operator="dispatcher-1", reason="Проверено")
        own = self.operations.dispatch_activity("dispatcher-3")
        self.assertEqual((own["claimed"], own["responses"], own["transferred"], own["closed"]), (1, 1, 1, 0))
        self.assertEqual(self.operations.dispatch_activity("dispatcher-1")["closed"], 1)
        exported = list(csv.DictReader(io.StringIO(self.operations.dispatch_history_csv("dispatcher-3").decode("utf-8-sig"))))
        self.assertEqual(len(exported), 3)
        self.assertTrue(all(row["actor_operator_id"] == "dispatcher-3" for row in exported))
        self.assertTrue(exported[0]["reason"].startswith("'="))

    def test_dispatcher_three_cannot_claim_unaddressed_other_sector(self):
        self.service.ingest_event({"event_id": "position-zone", "event_time": stamp(self.clock()), "sensor_id": "POS-V1", "type": "position",
                                   "demo": True, "payload": {"asset_id": "V1", "x": 20, "y": 50}})
        item = self.service.list_incidents()[0]
        with self.assertRaises(ApiError):
            self.action(item, "claim", operator="dispatcher-3")
        self.assertEqual(self.operations.dispatch_activity("dispatcher-3")["claimed"], 0)

    def test_shift_fresh_positions_stay_still_until_actual_gate_checks_then_move(self):
        runner = DemoRunner(self.service)
        runner._prepare("shift")
        baseline = None
        for elapsed in range(8):
            self.clock.advance()
            runner.emit_frame(elapsed)
            vehicles = {asset["id"]: (asset["x"], asset["y"]) for asset in self.service.list_assets() if asset["type"] == "vehicle"}
            baseline = baseline or vehicles
            shift = runner.status()["shift"]
            if elapsed < 5:
                self.assertEqual(vehicles, baseline)
                self.assertEqual(shift["transport_events_count"], 0)
                self.assertTrue(all(state["state"] == "awaiting_gate_checks" for state in runner.status()["vehicle_states"].values()))
                self.assertTrue(all(sensor["status"] == "online" for sensor in self.service.list_sensors()))
            if elapsed == 5:
                self.assertEqual(shift["gate_count"], 3)
                self.assertEqual(shift["phase"], "transport")
                self.assertEqual(vehicles, baseline)
            if elapsed == 7:
                self.assertNotEqual(vehicles, baseline)
                self.assertEqual(shift["transport_asset_ids"], ["V1", "V2", "V3"])
                self.assertEqual(shift["transport_events_count"], 9)
        self.assertEqual(self.service.list_incidents(), [])
        self.assertEqual(self.operations.journal()["total"], 3)

    def test_failed_gate_ingest_prevents_any_transport_and_source_reports_error(self):
        async def check():
            runner = DemoRunner(self.service, interval=0.005, monotonic=lambda: self.clock().timestamp())
            await runner.start("shift")
            before = {asset["id"]: (asset["x"], asset["y"]) for asset in self.service.list_assets() if asset["type"] == "vehicle"}
            actual = self.service.ingest_event

            def fail_gate(event, **kwargs):
                if event["type"] == "access":
                    raise RuntimeError("gate source failed")
                return actual(event, **kwargs)

            with patch.object(self.service, "ingest_event", side_effect=fail_gate):
                self.clock.advance(6)
                await asyncio.sleep(0.05)
            self.assertFalse(runner.running)
            self.assertEqual(runner.error, "gate source failed")
            self.assertEqual(runner.status()["shift"]["phase"], "error")
            self.assertEqual(runner.status()["shift"]["transport_events_count"], 0)
            after = {asset["id"]: (asset["x"], asset["y"]) for asset in self.service.list_assets() if asset["type"] == "vehicle"}
            self.assertEqual(before, after)
            await runner.stop()

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
