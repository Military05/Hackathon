"""Shared plan stays navigable after an asymmetric local layout revision."""
import json
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def inside(point, rectangle):
    return (rectangle["x"] < point[0] < rectangle["x"] + rectangle["width"]
            and rectangle["y"] < point[1] < rectangle["y"] + rectangle["height"])


def segment_crosses_interior(start, end, rectangle):
    """Clip against a slightly inset box; touching a boundary is permitted."""
    entry, exit_ = 0.0, 1.0
    for axis, key, size in ((0, "x", "width"), (1, "y", "height")):
        lower = rectangle[key] + 1e-7
        upper = rectangle[key] + rectangle[size] - 1e-7
        delta = end[axis] - start[axis]
        if abs(delta) < 1e-12:
            if not lower <= start[axis] <= upper:
                return False
            continue
        first, last = sorted(((lower - start[axis]) / delta, (upper - start[axis]) / delta))
        entry, exit_ = max(entry, first), min(exit_, last)
        if entry > exit_:
            return False
    return entry <= exit_


class SiteLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = json.loads((ROOT / "data/demo/site.json").read_text(encoding="utf-8"))

    def test_stable_ids_and_zone_contract(self):
        self.assertEqual({b["id"] for b in self.site["buildings"]}, {"O1", "W1", "W2", "P1", "P2", "G1"})
        self.assertEqual(set(self.site["routes"]), {"V1", "V2", "V3"})
        zones = {zone["id"]: zone["rectangle"] for zone in self.site["zones"]}
        self.assertEqual(zones["Z1"], {"x": 12, "y": 43, "width": 26, "height": 14})
        self.assertEqual(zones["Z2"], {"x": 64, "y": 44, "width": 23, "height": 12})
        self.assertTrue(inside((25, 50), zones["Z1"]))
        self.assertTrue(inside((20, 50), zones["Z1"]))
        self.assertFalse(inside((39, 58), zones["Z1"]))

    def test_buildings_have_varied_proportions_and_fit_bounds(self):
        rectangles = [building["rectangle"] for building in self.site["buildings"]]
        self.assertEqual(len({(r["width"], r["height"]) for r in rectangles}), len(rectangles))
        for building in self.site["buildings"]:
            with self.subTest(building=building["id"]):
                rectangle = building["rectangle"]
                self.assertGreater(rectangle["width"], 0)
                self.assertGreater(rectangle["height"], 0)
                self.assertGreaterEqual(rectangle["x"], 0)
                self.assertGreaterEqual(rectangle["y"], 0)
                self.assertLessEqual(rectangle["x"] + rectangle["width"], 100)
                self.assertLessEqual(rectangle["y"] + rectangle["height"], 100)
                self.assertTrue(building["short_name"].strip())
                self.assertLessEqual(len(building["short_name"]), len(building["name"]))
        by_id = {building["id"]: building["rectangle"] for building in self.site["buildings"]}
        self.assertNotEqual(by_id["W1"]["y"], by_id["P1"]["y"])
        self.assertNotEqual(by_id["W2"]["y"], by_id["P2"]["y"])
        self.assertLess(by_id["G1"]["width"] * by_id["G1"]["height"],
                        by_id["O1"]["width"] * by_id["O1"]["height"])

    def test_buildings_do_not_overlap_and_remain_in_their_areas(self):
        areas = {area["id"]: area for area in self.site["site_areas"]}
        for index, building in enumerate(self.site["buildings"]):
            rectangle = building["rectangle"]
            area = areas[building["site_area_id"]]["rectangle"]
            with self.subTest(building=building["id"]):
                self.assertGreaterEqual(rectangle["x"], area["x"])
                self.assertGreaterEqual(rectangle["y"], area["y"])
                self.assertLessEqual(rectangle["x"] + rectangle["width"], area["x"] + area["width"])
                self.assertLessEqual(rectangle["y"] + rectangle["height"], area["y"] + area["height"])
            for other in self.site["buildings"][index + 1:]:
                second = other["rectangle"]
                horizontal = min(rectangle["x"] + rectangle["width"], second["x"] + second["width"]) - max(rectangle["x"], second["x"])
                vertical = min(rectangle["y"] + rectangle["height"], second["y"] + second["height"]) - max(rectangle["y"], second["y"])
                self.assertFalse(horizontal > 0 and vertical > 0, (building["id"], other["id"]))

    def test_routes_use_shared_curved_road_centerlines_and_close(self):
        roads = {road["id"]: road for road in self.site["roads"]}
        route_roads = {"V1": "road-west", "V2": "road-east", "V3": "road-main"}
        for vehicle, road_id in route_roads.items():
            with self.subTest(vehicle=vehicle):
                route = self.site["routes"][vehicle]
                self.assertEqual(route, roads[road_id]["points"])
                self.assertEqual(route[0], route[-1])
                self.assertGreater(len(route), 20)
                diagonal = 0
                for start, end in zip(route, route[1:]):
                    self.assertGreater(math.dist(start, end), 0)
                    if start[0] != end[0] and start[1] != end[1]:
                        diagonal += 1
                self.assertGreater(diagonal, 15, "Curves must be sampled in the shared data, not only drawn by the UI")

    def test_roads_and_routes_never_cross_building_interiors(self):
        for road in self.site["roads"]:
            self.assertGreater(road["width"], 0)
            for point in road["points"]:
                self.assertTrue(all(math.isfinite(coordinate) and 0 <= coordinate <= 100 for coordinate in point))
            for start, end in zip(road["points"], road["points"][1:]):
                for building in self.site["buildings"]:
                    with self.subTest(road=road["id"], building=building["id"]):
                        self.assertFalse(segment_crosses_interior(start, end, building["rectangle"]))

    def test_normal_routes_do_not_enter_forbidden_zones(self):
        permissions = {item["asset_id"]: item for item in self.site["permissions"] if "asset_id" in item}
        for vehicle, route in self.site["routes"].items():
            for zone in self.site["zones"]:
                if zone["id"] in permissions[vehicle]["allowed_zone_ids"]:
                    continue
                for start, end in zip(route, route[1:]):
                    with self.subTest(vehicle=vehicle, zone=zone["id"]):
                        self.assertFalse(segment_crosses_interior(start, end, zone["rectangle"]))

    def test_fixed_sensor_anchors_are_in_their_responsibility_areas(self):
        areas = {area["id"]: area for area in self.site["site_areas"]}
        buildings = {building["id"]: building for building in self.site["buildings"]}
        for sensor in self.site["sensors"]:
            if "position" not in sensor:
                continue
            point = sensor["position"]["x"], sensor["position"]["y"]
            with self.subTest(sensor=sensor["id"]):
                self.assertTrue(all(0 <= value <= 100 for value in point))
                if sensor["type"] == "access":
                    rectangle = buildings[sensor["building_id"]]["rectangle"]
                    self.assertLessEqual(abs(point[1] - rectangle["y"] - rectangle["height"]), 2)
                    self.assertTrue(rectangle["x"] <= point[0] <= rectangle["x"] + rectangle["width"])
                else:
                    self.assertTrue(inside(point, areas[sensor["site_area_id"]]["rectangle"]))


if __name__ == "__main__":
    unittest.main()
