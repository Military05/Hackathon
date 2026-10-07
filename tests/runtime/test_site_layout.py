"""The factory campus is a connected, shared map and movement plan."""
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


def point_segment_distance(point, start, end):
    delta = [end[axis] - start[axis] for axis in range(2)]
    length_squared = sum(value * value for value in delta)
    if length_squared == 0:
        return math.dist(point, start)
    fraction = max(0, min(1, sum((point[axis] - start[axis]) * delta[axis] for axis in range(2)) / length_squared))
    return math.dist(point, [start[axis] + fraction * delta[axis] for axis in range(2)])


def rectangles_overlap(first, second):
    return (min(first['x'] + first['width'], second['x'] + second['width']) > max(first['x'], second['x'])
            and min(first['y'] + first['height'], second['y'] + second['height']) > max(first['y'], second['y']))


def collinear_overlap(first, second):
    a, b = first
    c, d = second
    delta = [b[axis] - a[axis] for axis in range(2)]
    cross = lambda p: delta[0] * (p[1] - a[1]) - delta[1] * (p[0] - a[0])
    if abs(cross(c)) > 1e-7 or abs(cross(d)) > 1e-7:
        return 0
    axis = 0 if abs(delta[0]) > abs(delta[1]) else 1
    first_range, second_range = sorted((a[axis], b[axis])), sorted((c[axis], d[axis]))
    return max(0, min(first_range[1], second_range[1]) - max(first_range[0], second_range[0]))


class SiteLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = json.loads((ROOT / "data/demo/site.json").read_text(encoding="utf-8"))

    def test_stable_ids_and_zone_contract(self):
        identifiers = {b['id'] for b in self.site['buildings']}
        self.assertTrue({"O1", "W1", "W2", "P1", "P2", "G1"} <= identifiers)
        self.assertGreaterEqual(len(identifiers), 14)
        self.assertEqual(len(identifiers), len(self.site['buildings']))
        self.assertEqual(set(self.site["routes"]), {"V1", "V2", "V3"})
        zones = {zone["id"]: zone["rectangle"] for zone in self.site["zones"]}
        self.assertEqual(zones["Z1"], {"x": 12, "y": 43, "width": 26, "height": 14})
        self.assertEqual(zones["Z2"], {"x": 64, "y": 44, "width": 23, "height": 12})
        self.assertTrue(inside((25, 50), zones["Z1"]))
        self.assertTrue(inside((20, 50), zones["Z1"]))
        self.assertFalse(inside((39, 58), zones["Z1"]))

    def test_buildings_have_varied_proportions_and_fit_bounds(self):
        rectangles = [building["rectangle"] for building in self.site["buildings"]]
        self.assertGreaterEqual(len({(r["width"], r["height"]) for r in rectangles}), 12)
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
                self.assertRegex(building['color'], r'^#[0-9a-fA-F]{6}$')
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

    def test_routes_use_shared_road_centerlines_and_close(self):
        road_segments = [(start, end) for road in self.site['roads']
                         for start, end in zip(road['points'], road['points'][1:])]
        for vehicle, route in self.site['routes'].items():
            with self.subTest(vehicle=vehicle):
                self.assertEqual(route[0], route[-1])
                self.assertGreater(len(route), 10)
                for start, end in zip(route, route[1:]):
                    self.assertGreater(math.dist(start, end), 0)
                    subdivisions = max(1, math.ceil(math.dist(start, end) / .5))
                    for index in range(subdivisions + 1):
                        point = [start[axis] + (end[axis] - start[axis]) * index / subdivisions
                                 for axis in range(2)]
                        self.assertLess(min(point_segment_distance(point, a, b) for a, b in road_segments), 1e-7,
                                        (vehicle, point, 'Movement must use real road centerlines'))

    def test_all_roads_connect_at_shared_vertices(self):
        roads = self.site['roads']
        vertices = [{tuple(point) for point in road['points']} for road in roads]
        reached = {0}
        while True:
            neighbours = {index for index, points in enumerate(vertices)
                          if any(points & vertices[known] for known in reached)}
            if neighbours <= reached:
                break
            reached |= neighbours
        self.assertEqual(reached, set(range(len(roads))),
                         'Disconnected roads or drawn-only intersections cannot carry vehicles')
        diagonal = sum(start[0] != end[0] and start[1] != end[1]
                       for road in roads for start, end in zip(road['points'], road['points'][1:]))
        self.assertGreaterEqual(diagonal, 20, 'Rounded corners are sampled in the shared geometry')

    def test_road_segments_are_not_drawn_on_top_of_each_other(self):
        segments = [(road['id'], start, end) for road in self.site['roads']
                    for start, end in zip(road['points'], road['points'][1:])]
        for index, (road_id, start, end) in enumerate(segments):
            for other_id, first, second in segments[index + 1:]:
                self.assertLessEqual(collinear_overlap((start, end), (first, second)), 1e-7,
                                     (road_id, other_id, 'Each road centerline stretch is stored once'))
                delta = [end[axis] - start[axis] for axis in range(2)]
                other_delta = [second[axis] - first[axis] for axis in range(2)]
                denominator = delta[0] * other_delta[1] - delta[1] * other_delta[0]
                if abs(denominator) < 1e-10:
                    continue
                offset = [first[axis] - start[axis] for axis in range(2)]
                t = (offset[0] * other_delta[1] - offset[1] * other_delta[0]) / denominator
                u = (offset[0] * delta[1] - offset[1] * delta[0]) / denominator
                if -1e-7 <= t <= 1 + 1e-7 and -1e-7 <= u <= 1 + 1e-7:
                    self.assertTrue(min(abs(t), abs(t - 1)) <= 1e-7 and min(abs(u), abs(u - 1)) <= 1e-7,
                                    (road_id, other_id, 'Intersections must be shared graph vertices'))

    def test_zone_colors_reflect_permissions_and_hard_prohibition_has_no_allowlist(self):
        zones = {zone['id']: zone for zone in self.site['zones']}
        self.assertEqual(zones['Z1']['kind'], 'permit')
        self.assertEqual(zones['Z2']['kind'], 'permit')
        self.assertEqual(zones['Z3']['kind'], 'forbidden')
        self.assertTrue(all(zone['kind'] in ('permit', 'forbidden') for zone in zones.values()))
        for permission in self.site['permissions']:
            self.assertNotIn('Z3', permission['allowed_zone_ids'])
        for index, zone in enumerate(self.site['zones']):
            for other in self.site['zones'][index + 1:]:
                self.assertFalse(rectangles_overlap(zone['rectangle'], other['rectangle']))
            for building in self.site['buildings']:
                self.assertFalse(rectangles_overlap(zone['rectangle'], building['rectangle']))

    def test_sector_focus_is_geographical_and_gate_profile_has_a_small_local_view(self):
        sectors = {sector['id']: sector for sector in self.site['sectors']}
        profiles = {profile['id']: profile for profile in self.site['operator_profiles']}
        self.assertEqual(profiles['dispatcher-3']['sector_id'], 'coordination')
        self.assertEqual(sectors['coordination']['name'], 'КПП')
        gate = next(building['rectangle'] for building in self.site['buildings'] if building['id'] == 'G1')
        gate_focus = sectors['coordination']['focus_bounds']
        self.assertTrue(inside((gate['x'] + gate['width'] / 2, gate['y'] + gate['height'] / 2), gate_focus))
        self.assertLess(gate_focus['width'] * gate_focus['height'], 1000)
        for sector in sectors.values():
            box = sector['focus_bounds']
            self.assertGreater(box['width'], 0)
            self.assertGreater(box['height'], 0)
            self.assertLess(box['height'], 100)
            self.assertTrue(sector['map_regions'])
            self.assertLessEqual(box['x'] + box['width'], 100)
            self.assertLessEqual(box['y'] + box['height'], 100)

    def test_gate_source_and_shift_employees_have_preserved_real_permission_checks(self):
        sensor = next(sensor for sensor in self.site['sensors'] if sensor['id'] == 'ACCESS-G1')
        self.assertEqual((sensor['type'], sensor['building_id'], sensor['site_area_id']), ('access', 'G1', 'checkpoint'))
        employees = {asset['id'] for asset in self.site['assets'] if asset['type'] == 'employee'}
        permissions = {permission['employee_id']: permission for permission in self.site['permissions'] if 'employee_id' in permission}
        self.assertEqual(self.site['shift_employees'], ['U1', 'U2', 'U3'])
        self.assertTrue(set(self.site['shift_employees']) <= employees)
        for employee in self.site['shift_employees']:
            self.assertIn('G1', permissions[employee]['allowed_building_ids'])
        self.assertIn('U4', employees)
        self.assertNotIn('G1', permissions['U4']['allowed_building_ids'])

    def test_roads_and_routes_never_cross_building_interiors(self):
        for road in self.site["roads"]:
            self.assertGreater(road["width"], 0)
            for point in road["points"]:
                self.assertTrue(all(math.isfinite(coordinate) and 0 <= coordinate <= 100 for coordinate in point))
            for start, end in zip(road["points"], road["points"][1:]):
                for building in self.site["buildings"]:
                    with self.subTest(road=road["id"], building=building["id"]):
                        box = building['rectangle']
                        padding = road['width'] / 2
                        road_clearance = dict(x=box['x'] - padding, y=box['y'] - padding,
                                              width=box['width'] + 2 * padding,
                                              height=box['height'] + 2 * padding)
                        self.assertFalse(segment_crosses_interior(start, end, road_clearance),
                                         'The full road width must stay outside buildings')

    def test_normal_routes_do_not_enter_forbidden_zones(self):
        permissions = {item["asset_id"]: item for item in self.site["permissions"] if "asset_id" in item}
        for vehicle, route in self.site["routes"].items():
            for zone in self.site["zones"]:
                if zone["id"] in permissions[vehicle]["allowed_zone_ids"]:
                    continue
                for start, end in zip(route, route[1:]):
                    with self.subTest(vehicle=vehicle, zone=zone["id"]):
                        self.assertFalse(segment_crosses_interior(start, end, zone["rectangle"]))

    def test_factory_spans_campus_and_office_is_next_to_gate(self):
        buildings = self.site['buildings']
        boxes = [building['rectangle'] for building in buildings]
        self.assertGreater(max(r['x'] + r['width'] for r in boxes) - min(r['x'] for r in boxes), 85)
        self.assertGreater(max(r['y'] + r['height'] for r in boxes) - min(r['y'] for r in boxes), 85)
        categories = {building['category'] for building in buildings}
        self.assertTrue({'office', 'checkpoint', 'warehouse', 'workshop', 'service', 'utility'} <= categories)
        indexed = {building['id']: building['rectangle'] for building in buildings}
        centers = {identifier: [box['x'] + box['width'] / 2, box['y'] + box['height'] / 2]
                   for identifier, box in indexed.items()}
        self.assertLess(math.dist(centers['O1'], centers['G1']), 23)
        self.assertGreater(centers['O1'][1], 80)
        for horizontal in (False, True):
            for vertical in (False, True):
                self.assertGreaterEqual(sum((point[0] > 50) == horizontal and (point[1] > 50) == vertical
                                            for point in centers.values()), 3)

    def test_different_sectors_have_no_overlapping_area_interiors(self):
        areas = [area for area in self.site['site_areas'] if area.get('rectangle')]
        for index, area in enumerate(areas):
            for other in areas[index + 1:]:
                if area['responsible_sector_id'] != other['responsible_sector_id']:
                    self.assertFalse(rectangles_overlap(area['rectangle'], other['rectangle']),
                                     (area['id'], other['id']))

    def test_fault_branch_has_real_join_and_enters_only_its_zone(self):
        branch = self.site['demo_fault_routes']['V1']
        self.assertIn(branch['points'][0], self.site['routes']['V1'])
        zone = next(zone for zone in self.site['zones'] if zone['id'] == branch['zone_id'])
        self.assertFalse(inside(branch['points'][0], zone['rectangle']))
        self.assertTrue(inside(branch['points'][-1], zone['rectangle']))
        road = next(road for road in self.site['roads'] if road['id'] == 'loading-spur')
        self.assertEqual(branch['points'], road['points'])

    def test_route_stops_are_on_the_corresponding_route(self):
        buildings = {building['id'] for building in self.site['buildings']}
        for vehicle, stops in self.site['route_stops'].items():
            route = self.site['routes'][vehicle]
            for stop in stops:
                self.assertIn(stop['destination'], buildings)
                self.assertGreater(stop['seconds'], 0)
                self.assertLess(min(point_segment_distance(stop['position'], a, b)
                                    for a, b in zip(route, route[1:])), 1e-7, (vehicle, stop))

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
