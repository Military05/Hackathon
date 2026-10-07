"""Small acceptance set: measured routes, vehicle pairs and typed zone rules."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from src.core.demo_traffic import DemoRunner
from src.core.service import Service, stamp


ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.initial = datetime.now(timezone.utc)
        self.elapsed = 0

    def __call__(self):
        return self.initial + timedelta(seconds=self.elapsed)


class VehicleSafetyAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.site = json.loads((ROOT / "data/demo/site.json").read_text(encoding="utf-8"))
        self.site["safety_routes"] = {"V1": [[35, 74], [50, 74]], "V2": [[90, 4], [95, 4]], "V3": [[40, 65], [40, 90]]}
        self.service = self.create_service("safety")
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def create_service(self, name):
        path = Path(self.temp.name) / (name + ".json")
        path.write_text(json.dumps(self.site, ensure_ascii=False), encoding="utf-8")
        return Service(Path(self.temp.name) / (name + ".db"), path, clock=self.clock)

    def position(self, vehicle, x, y, service=None, at=None):
        self.sequence += 1
        service = service or self.service
        event = {"event_id": "safety-" + str(self.sequence).zfill(5), "event_time": stamp(self.clock.initial + timedelta(seconds=self.clock.elapsed if at is None else at)),
                 "sensor_id": "POS-" + vehicle, "type": "position", "demo": True,
                 "payload": {"asset_id": vehicle, "x": x, "y": y}}
        service.ingest_event(event)
        return event

    def incidents(self, kind, service=None):
        return [item for item in (service or self.service).list_incidents() if item["type"] == kind]

    def test_normal_and_shift_have_no_safety_spam_on_distinct_real_event_routes(self):
        routes = {"V1": [[3, 4], [10, 4], [10, 6], [3, 6], [3, 4]],
                  "V2": [[90, 4], [95, 4], [95, 6], [90, 6], [90, 4]],
                  "V3": [[42, 82], [58, 82], [58, 84], [42, 84], [42, 82]]}
        self.site["demo_routes"] = self.site["safety_routes"] = routes
        service = self.create_service("normal")
        runner = DemoRunner(service)
        for scenario, duration in (("normal", 60), ("shift", 12)):
            runner._prepare(scenario)
            for elapsed in range(duration):
                self.clock.elapsed += 1
                runner.emit_frame(elapsed)
                service.tick()
            self.assertEqual(service.list_incidents(), [])
        self.assertEqual(runner.status()["shift"]["gate_count"], 3)

    def test_route_needs_two_samples_duplicate_does_not_count_and_stale_never_restores(self):
        self.position("V1", 38, 74)
        self.clock.elapsed = 1
        first = self.position("V1", 38, 79)
        self.service.ingest_event(first)
        self.assertEqual(self.incidents("route_deviation"), [])
        self.clock.elapsed = 2
        self.position("V1", 39, 79)
        item = self.incidents("route_deviation")[0]
        self.assertEqual(item["severity"], "warning")
        self.assertEqual(len(item["evidence_event_ids"]), 2)
        self.clock.elapsed = 8
        self.service.tick()
        self.assertEqual(self.incidents("route_deviation")[0]["condition_state"], "unknown")
        for elapsed in (9, 10):
            self.clock.elapsed = elapsed
            self.position("V1", 40, 74)
        self.assertEqual(len(self.incidents("route_deviation")), 1)
        self.assertEqual(self.incidents("route_deviation")[0]["condition_state"], "restored")

    def test_pair_overlap_and_timestamped_crossing_create_one_case_then_restore(self):
        for mode in ("overlap", "crossing"):
            self.clock.elapsed = 0
            service = self.create_service(mode)
            self.position("V1", 40 if mode == "overlap" else 38, 74, service)
            first = self.position("V3", 40, 74.5 if mode == "overlap" else 72, service)
            if mode == "crossing":
                self.assertEqual(self.incidents("collision", service), [])
                self.clock.elapsed = 1
                self.position("V1", 42, 74, service)
                self.position("V3", 40, 76, service)
            cases = self.incidents("collision", service)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["severity"], "critical")
            self.assertEqual((cases[0]["asset_id"], cases[0]["other_asset_id"]), ("V1", "V3"))
            self.assertEqual(cases[0]["details"]["detection_mode"], "measured_overlap" if mode == "overlap" else "measured_swept_segments")
            service.ingest_event(first)
            self.assertEqual(len(self.incidents("collision", service)), 1)
            for elapsed in (2, 3, 4):
                self.clock.elapsed = elapsed
                self.position("V1", 42 + elapsed * 2, 74, service)
                self.position("V3", 40, 76 + elapsed * 2, service)
            self.assertEqual(len(self.incidents("collision", service)), 1)
            self.assertEqual(self.incidents("collision", service)[0]["condition_state"], "restored")

    def test_spatial_intersection_at_different_times_and_stale_peer_are_not_collisions(self):
        for elapsed, a_x, b_y in ((0, 38, 68), (1, 42, 72), (2, 46, 76)):
            self.clock.elapsed = elapsed
            self.position("V1", a_x, 74)
            self.position("V3", 40, b_y)
        self.assertEqual(self.incidents("collision"), [])
        stale = self.create_service("stale")
        self.clock.elapsed = 0
        self.position("V3", 40, 74, stale)
        self.clock.elapsed = 3
        self.position("V1", 40.5, 74, stale)
        self.assertEqual(self.incidents("collision", stale), [])

    def test_typed_red_zone_overrides_allowlist_only_for_matching_type_and_hard_zone_denies_all(self):
        self.site["zones"].append({"id": "TYPED", "kind": "restricted", "restricted_vehicle_types": ["forklift"],
            "name": "Запрет погрузчиков", "site_area_id": "workshop-1", "rectangle": {"x": 5, "y": 64, "width": 15, "height": 6}})
        for permission in self.site["permissions"]:
            if permission.get("asset_id") in ("V1", "V3"):
                permission["allowed_zone_ids"].extend(["TYPED", "Z3"])
        service = self.create_service("types")
        self.position("V1", 7, 66, service)
        self.position("V3", 18, 66, service)
        typed = [item for item in self.incidents("forbidden_zone", service) if item["zone_id"] == "TYPED"]
        self.assertEqual([item["asset_id"] for item in typed], ["V1"])
        self.clock.elapsed = 1
        self.position("V1", 89, 50, service)
        hard = [item for item in self.incidents("forbidden_zone", service) if item["zone_id"] == "Z3"]
        self.assertEqual([item["asset_id"] for item in hard], ["V1"])


if __name__ == "__main__":
    unittest.main()
