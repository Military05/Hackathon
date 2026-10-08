"""Real demo Events, road interpolation, fault restoration and exclusive lifecycle."""
import asyncio
from datetime import datetime, timedelta, timezone
import math
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.core.demo_traffic import ArcRoute, DemoRunner, Journey, SCENARIOS
from src.core.geometry import rectangle_contains
from tests.runtime.isolated_app import create_app
from src.core.service import Service


ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.initial = datetime.now(timezone.utc)
        self.elapsed = 0

    def __call__(self):
        return self.initial + timedelta(seconds=self.elapsed)


class RouteTests(unittest.TestCase):
    def test_arc_length_uses_distance_not_sample_index(self):
        route = ArcRoute([[0, 0], [1, 0], [11, 0], [11, 4]])
        self.assertEqual(route.point(5, loop=False), (5, 0))
        self.assertEqual(route.point(13, loop=False), (11, 2))
        self.assertEqual(route.point(20, loop=False), (11, 4))
        self.assertEqual(route.nearest_distance((5, 3)), 5)

    def test_closed_route_does_not_accumulate_frame_drift(self):
        route = ArcRoute([[0, 0], [10, 0], [10, 10], [0, 0]])
        expected = route.point(7)
        actual = route.point(10000 * route.length + 7)
        self.assertAlmostEqual(expected[0], actual[0], places=9)
        self.assertAlmostEqual(expected[1], actual[1], places=9)
        self.assertEqual(route.point(route.length), (0, 0))

    def test_stationary_loading_keeps_exact_road_coordinate(self):
        route = ArcRoute([[0, 0], [10, 0], [0, 0]])
        journey = Journey(route, 5, speed=2, pause=3)
        self.assertEqual(journey.position(0), (5, 0))
        self.assertEqual(journey.position(2), (5, 0))
        self.assertEqual(journey.state(2), "loading")
        self.assertEqual(journey.position(5), (9, 0))
        self.assertEqual(journey.state(5), "moving")
        self.assertEqual(journey.position(journey.period + 2), (5, 0))

    def test_invalid_and_repeated_route_points(self):
        self.assertEqual(ArcRoute([[1, 1], [1, 1], [2, 1]]).length, 1)
        for points in ([[1, 1]], [[1, 1], [1, 1]], [[-1, 0], [2, 0]], [[0, 0], [math.nan, 0]]):
            with self.assertRaises(ValueError):
                ArcRoute(points)


class TrafficAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        site = json.loads((ROOT / "data/demo/site.json").read_text(encoding="utf-8"))
        if not any(sensor["id"] == "ACCESS-G1" for sensor in site["sensors"]):
            site["sensors"].append({"id": "ACCESS-G1", "type": "access", "building_id": "G1", "site_area_id": "checkpoint"})
        if not any(asset["id"] == "U4" for asset in site["assets"]):
            site["assets"].append({"id": "U4", "type": "employee", "name": "Гость без допуска"})
            site["permissions"].append({"employee_id": "U4", "allowed_building_ids": [], "allowed_zone_ids": []})
        site_path = Path(self.temp.name) / "site.json"
        site_path.write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
        self.service = Service(Path(self.temp.name) / "traffic.db", site_path, clock=self.clock)
        self.runner = DemoRunner(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def run_frames(self, scenario, stop):
        self.runner._prepare(scenario)
        for elapsed in range(stop + 1):
            self.clock.elapsed = elapsed
            self.runner.emit_frame(elapsed)
            self.service.tick()

    def test_normal_route_events_are_shared_fresh_and_do_not_raise_incidents(self):
        self.run_frames("normal", 125)
        self.assertEqual(self.service.list_incidents(), [])
        self.assertEqual(self.runner.event_count, 126 * len(self.service.sensors_by_id))
        self.assertTrue(all(sensor["status"] == "online" for sensor in self.service.list_sensors()))
        for asset in self.service.list_assets():
            if asset["type"] != "vehicle":
                continue
            self.assertEqual(asset["position_state"], "fresh")
            route = self.runner._journeys[asset["id"]].route
            self.assertLess(math.dist((asset["x"], asset["y"]), route.point(route.nearest_distance((asset["x"], asset["y"])))), 0.001)
            self.assertFalse(any(rectangle_contains(asset["x"], asset["y"], building["rectangle"]) for building in self.service.site["buildings"]))
        self.assertEqual(self.service.model_observations(), [])

    def test_vehicle_progress_is_continuous_in_every_nonfault_scenario(self):
        for scenario in ("normal", "logistics", "shift", "service"):
            self.runner._prepare(scenario)
            for asset_id, journey in self.runner._journeys.items():
                previous = journey.position(0)
                for elapsed in range(1, 250):
                    position = journey.position(elapsed)
                    self.assertLessEqual(math.dist(previous, position), journey.speed + 1e-7)
                    previous = position

    def test_forbidden_zone_actual_ingress_and_two_exit_samples_restore_one_episode(self):
        self.runner._prepare("forbidden-zone")
        active_seen = False
        previous = None
        for elapsed in range(32):
            self.clock.elapsed = elapsed
            self.runner.emit_frame(elapsed)
            self.service.tick()
            vehicle = next(asset for asset in self.service.list_assets() if asset["id"] == "V1")
            position = (vehicle["x"], vehicle["y"])
            if previous:
                self.assertLessEqual(math.dist(previous, position), 1.801)
            previous = position
            active_seen |= any(incident["condition_active"] for incident in self.service.list_incidents())
        incidents = [item for item in self.service.list_incidents() if item["type"] == "forbidden_zone"]
        self.assertTrue(active_seen)
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(incident["type"], "forbidden_zone")
        self.assertEqual(incident["condition_state"], "restored")
        self.assertFalse(incident["condition_active"])
        self.assertGreaterEqual(len(incident["evidence_event_ids"]), 2)

    def test_one_confirmed_access_case_and_authorized_shift_do_not_fake_model_output(self):
        self.run_frames("unauthorized-access", 40)
        incidents = self.service.list_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["type"], "unauthorized_access")
        self.assertEqual(incidents[0]["employee_id"], "U4")
        self.assertEqual(incidents[0]["building_id"], "G1")
        self.assertFalse(incidents[0]["condition_active"])
        events = self.service.event_history(limit=500)
        self.assertEqual(sum(event["type"] == "access" for event in events), 2)
        self.clock.elapsed = 41
        self.runner._prepare("shift")
        for elapsed in range(21):
            self.clock.elapsed = 41 + elapsed
            self.runner.emit_frame(elapsed)
            self.service.tick()
        self.assertEqual(len(self.service.list_incidents()), 1)
        self.assertEqual(self.service.model_observations(), [])

    def test_offline_suppresses_actual_source_and_restores_only_on_returned_heartbeat(self):
        self.runner._prepare("sensor-offline")
        for elapsed in range(13):
            self.clock.elapsed = elapsed
            self.runner.emit_frame(elapsed)
            self.service.tick()
        health = self.service.sensor_health("HB-QA")
        self.assertEqual(health["status"], "offline")
        self.assertAlmostEqual(health["age_seconds"], 8, delta=0.001)
        incident = self.service.list_incidents()[0]
        self.assertEqual(incident["sensor_id"], "HB-QA")
        self.assertTrue(incident["condition_active"])
        self.assertTrue(all(asset["position_state"] == "fresh" for asset in self.service.list_assets() if asset["type"] == "vehicle"))
        self.clock.elapsed = 17
        self.runner.emit_frame(17)
        self.service.tick()
        self.assertEqual(self.service.sensor_health("HB-QA")["status"], "online")
        self.assertFalse(self.service.list_incidents()[0]["condition_active"])

    def test_simultaneous_uses_four_real_rule_types_and_finishes_fault_episode(self):
        self.run_frames("simultaneous", 32)
        incidents = self.service.list_incidents()
        self.assertEqual({incident["type"] for incident in incidents}, {"forbidden_zone", "unauthorized_access", "sensor_offline", "route_deviation"})
        self.assertEqual(len(incidents), 4)
        self.assertTrue(all(not incident["condition_active"] for incident in incidents))
        self.assertTrue(all(incident["demo"] for incident in incidents))


class TrafficLifecycle(unittest.TestCase):
    def test_first_frame_failure_is_reported_and_does_not_leave_a_task(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp) / "failure.db", ROOT / "data/demo/site.json")

            async def check():
                runner = DemoRunner(service)
                with patch.object(service, "ingest_event", side_effect=RuntimeError("source failed")):
                    with self.assertRaisesRegex(ValueError, "source failed"):
                        await runner.start("normal")
                self.assertFalse(runner.running)
                self.assertEqual(runner.error, "source failed")
                self.assertIsNone(runner._task)
                self.assertEqual(runner.event_count, 0)

            asyncio.run(check())

    def test_start_switch_stop_have_one_task_and_no_late_events_after_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp) / "lifecycle.db", ROOT / "data/demo/site.json")

            async def check():
                runner = DemoRunner(service, interval=0.015)
                await runner.start("normal")
                first_task = runner._task
                self.assertTrue(runner.status()["running"])
                await runner.start("service")
                self.assertTrue(first_task.done())
                self.assertIsNot(runner._task, first_task)
                self.assertEqual(runner.status()["scenario"], "service")
                await runner.stop()
                count = runner.event_count
                await asyncio.sleep(0.04)
                self.assertEqual(runner.event_count, count)
                self.assertIsNone(runner._task)
                self.assertFalse(runner.status()["running"])
                await runner.stop()
                with self.assertRaises(ValueError):
                    await runner.start("not-a-scenario")
                self.assertFalse(runner.running)
                self.assertEqual(runner.error, "Unsupported traffic scenario")

            asyncio.run(check())

    def test_api_autostart_and_disabled_mode_preserve_other_extensions(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "DISPATCH_ENABLE_SIMULATOR": "0", "DISPATCH_ENABLE_AGENT": "0", "DISPATCH_ENABLE_ML": "0",
            "DISPATCH_ENABLE_DEMO_TRAFFIC": "1", "DEMO_AUTOSTART": "1",
        }):
            app = create_app(enable_auth=False, db_path=Path(temp) / "api.db")
            with TestClient(app) as client:
                status = client.get("/api/demo/status").json()
                self.assertTrue(status["running"])
                self.assertTrue(status["available"])
                self.assertEqual(status["source"], "builtin_factory_traffic")
                self.assertEqual(set(status["scenarios"]), set(SCENARIOS))
                self.assertGreaterEqual(status["event_count"], 5)
                headers = {"X-Demo-Operator": "dispatcher-1"}
                self.assertEqual(client.post("/api/demo/start", json={"scenario": "absent"}, headers=headers).status_code, 422)
                stopped = client.post("/api/demo/stop", json={}, headers=headers).json()
                self.assertFalse(stopped["running"])
                response = client.post("/api/demo/start", json={"scenario": "logistics"}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                started = response.json()
                self.assertTrue(started["running"])
                self.assertEqual(started["scenario"], "logistics")
                health = client.get("/api/health").json()
                self.assertEqual(health["ml"]["status"], "unavailable")
                self.assertEqual(health["agent"]["status"], "unavailable")
            self.assertFalse(app.state.demo.running)
            with patch.dict(os.environ, {"DISPATCH_ENABLE_DEMO_TRAFFIC": "0"}):
                with TestClient(create_app(enable_auth=False, db_path=Path(temp) / "disabled.db", enable_scheduler=False)) as client:
                    self.assertFalse(client.get("/api/demo/status").json()["available"])
                    response = client.post("/api/demo/start", json={"scenario": "normal"}, headers=headers)
                    self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
