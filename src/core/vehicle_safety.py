"""Small deterministic rules over measured positions, separate from ML and rendering."""
import json
import math

from src.core.service import canonical, parse_time, stamp


def route_distance(point, points):
    distance = math.inf
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = dx * dx + dy * dy
        fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length)) if length else 0.0
        distance = min(distance, math.hypot(point[0] - start[0] - fraction * dx, point[1] - start[1] - fraction * dy))
    return distance


def swept_distance(a0, a1, b0, b1):
    """Minimum separation of two timestamped measured segments over shared time.

    Straight movement between samples is an explicit demonstration assumption.
    Merely intersecting road drawings or client animation never triggers this rule.
    """
    begin, end = max(a0[2], b0[2]), min(a1[2], b1[2])
    if end < begin or a1[2] <= a0[2] or b1[2] <= b0[2]:
        return None

    def at(start, finish, when):
        ratio = (when - start[2]) / (finish[2] - start[2])
        return (start[0] + (finish[0] - start[0]) * ratio, start[1] + (finish[1] - start[1]) * ratio)

    a_begin, b_begin = at(a0, a1, begin), at(b0, b1, begin)
    a_end, b_end = at(a0, a1, end), at(b0, b1, end)
    relative = (a_begin[0] - b_begin[0], a_begin[1] - b_begin[1])
    change = ((a_end[0] - b_end[0]) - relative[0], (a_end[1] - b_end[1]) - relative[1])
    square = change[0] ** 2 + change[1] ** 2
    fraction = max(0.0, min(1.0, -(relative[0] * change[0] + relative[1] * change[1]) / square)) if square else 0.0
    return math.hypot(relative[0] + change[0] * fraction, relative[1] + change[1] * fraction)


class VehicleSafety:
    def __init__(self, service):
        self.service = service
        self.routes = service.site.get("safety_routes", {})
        self.enabled = bool(self.routes)
        self.config = service.site.get("vehicle_safety", {})
        self.route_threshold = float(self.config.get("route_distance_units", 2.0))
        self.segment_window = float(self.config.get("collision_window_seconds", 2.0))

    def _metadata(self, db, key):
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _save(self, db, key, state):
        db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical(state)))

    def _fresh(self, state):
        return bool(state and state.get("last_seen") and state.get("position_state") != "unknown"
                    and 0 <= (self.service.clock() - parse_time(state["last_seen"])).total_seconds()
                    < self.service.config["position_stale_seconds"])

    def _unknown(self, db, incident):
        if incident:
            incident["condition_state"] = "unknown"
            incident["details"]["exit_samples"] = 0
            self.service._save_incident(db, incident)

    def observe(self, db, event, previous):
        if not self.enabled:
            return
        asset_id = event["payload"]["asset_id"]
        current = self.service._load(db, "asset_state", "asset_id", asset_id)
        self._route(db, event, previous, current)
        for asset in self.service.site["assets"]:
            if asset["type"] == "vehicle" and asset["id"] != asset_id:
                self._pair(db, asset_id, asset["id"])

    def _route(self, db, event, previous, state):
        asset_id = event["payload"]["asset_id"]
        route = self.routes.get(asset_id)
        points = route.get("points", []) if isinstance(route, dict) else route
        if not points or len(points) < 2:
            return
        key, memory_key = "route:" + asset_id, "safety-route:" + asset_id
        incident = self.service._ongoing(db, key)
        memory = self._metadata(db, memory_key)
        fresh = self._fresh(state)
        gap = previous and (parse_time(state["last_seen"]) - parse_time(previous["last_seen"])).total_seconds() >= self.service.config["position_stale_seconds"]
        if not fresh:
            self._unknown(db, incident)
            self._save(db, memory_key, {"outside_count": 0})
            return
        distance = route_distance((state["x"], state["y"]), points)
        outside = distance > self.route_threshold
        count = (0 if gap else memory.get("outside_count", 0)) + 1 if outside else 0
        if outside:
            if not incident and count >= 2:
                evidence = [memory.get("last_event_id"), event["event_id"]]
                incident = self.service._new_incident(db, "route_deviation", key, state["site_area_id"],
                    [identifier for identifier in evidence if identifier], asset_id=asset_id, sensor_id=event["sensor_id"],
                    details={"route_id": asset_id, "threshold_units": self.route_threshold, "exit_samples": 0})
            if incident:
                incident["condition_state"], incident["condition_active"] = "active", True
                incident["details"].update(distance_units=round(distance, 3), outside_samples=count, exit_samples=0)
                self.service._evidence(incident, event["event_id"])
                self.service._save_incident(db, incident)
        elif incident:
            if gap or incident["condition_state"] == "unknown":
                incident["details"]["exit_samples"] = 0
            incident["condition_state"] = "active"
            incident["details"]["distance_units"] = round(distance, 3)
            incident["details"]["exit_samples"] = incident["details"].get("exit_samples", 0) + 1
            self.service._evidence(incident, event["event_id"])
            if incident["details"]["exit_samples"] >= 2:
                self.service._restore(db, incident, event["event_id"])
            else:
                self.service._save_incident(db, incident)
        self._save(db, memory_key, {"outside_count": count, "last_event_id": event["event_id"]})

    def _previous_sample(self, db, state):
        # A new demonstration run intentionally resets its positions. Do not
        # reinterpret that reset as an observed journey across the factory.
        prefix = state["event_id"].rsplit("-", 1)[0] if state["event_id"].startswith("factory-") else None
        condition = " AND event_id LIKE ?" if prefix else ""
        values = (state["asset_id"], state["last_seen"], state["last_seen"], state["event_id"])
        if prefix:
            values += (prefix + "-%",)
        row = db.execute("""SELECT body FROM events WHERE asset_id=? AND type='position'
            AND (event_time<? OR (event_time=? AND event_id<?))
            """ + condition + " ORDER BY event_time DESC,event_id DESC LIMIT 1", values).fetchone()
        return json.loads(row[0]) if row else None

    def _radius(self, asset_id):
        asset = self.service.assets_by_id[asset_id]
        radius = float(asset.get("collision_radius", asset.get("radius", 0.5)))
        return min(2.0, max(0.1, radius))

    def _pair(self, db, first, second):
        first, second = sorted((first, second))
        key, memory_key = "collision:" + first + ":" + second, "safety-pair:" + first + ":" + second
        incident = self.service._ongoing(db, key)
        states = [self.service._load(db, "asset_state", "asset_id", identifier) for identifier in (first, second)]
        if not all(self._fresh(state) for state in states):
            self._unknown(db, incident)
            memory = self._metadata(db, memory_key)
            memory["clear_count"] = 0
            self._save(db, memory_key, memory)
            return
        a, b = states
        prefixes = [state["event_id"].rsplit("-", 1)[0] for state in states if state["event_id"].startswith("factory-")]
        if len(prefixes) == 2 and prefixes[0] != prefixes[1]:
            self._unknown(db, incident)
            return
        times = [parse_time(state["last_seen"]).timestamp() for state in states]
        if abs(times[0] - times[1]) > self.segment_window:
            self._unknown(db, incident)
            return
        common = min(times)
        memory = self._metadata(db, memory_key)
        # One source arriving earlier than its peer cannot double-count recovery.
        if common <= memory.get("last_checked_time", -math.inf):
            return
        previous = [self._previous_sample(db, state) for state in states]
        distance = None
        mode = "measured_overlap"
        evidence = [a["event_id"], b["event_id"]]
        if all(previous):
            old_times = [parse_time(event["event_time"]).timestamp() for event in previous]
            durations = [times[index] - old_times[index] for index in (0, 1)]
            if all(0 < duration <= self.segment_window for duration in durations):
                segments = [(previous[index]["payload"]["x"], previous[index]["payload"]["y"], old_times[index]) for index in (0, 1)]
                distance = swept_distance(segments[0], (a["x"], a["y"], times[0]), segments[1], (b["x"], b["y"], times[1]))
                if distance is not None:
                    mode = "measured_swept_segments"
                    evidence = [previous[0]["event_id"], a["event_id"], previous[1]["event_id"], b["event_id"]]
        if distance is None and abs(times[0] - times[1]) <= 0.25:
            distance = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
        if distance is None:
            return
        threshold = float(self.config.get("collision_distance_units", self._radius(first) + self._radius(second)))
        if distance <= threshold:
            if not incident:
                area = a["site_area_id"] if a["site_area_id"] != "unknown" else b["site_area_id"]
                incident = self.service._new_incident(db, "collision", key, area, list(dict.fromkeys(evidence)),
                    asset_id=first, other_asset_id=second, sensor_id="POS-" + first,
                    details={"asset_ids": [first, second], "threshold_units": threshold, "exit_samples": 0,
                             "assumption": "Прямое движение между близкими по времени показаниями в демонстрационной модели."})
            incident["condition_state"], incident["condition_active"] = "active", True
            incident["details"].update(distance_units=round(distance, 3), detection_mode=mode, exit_samples=0)
            for identifier in evidence:
                self.service._evidence(incident, identifier)
            self.service._save_incident(db, incident)
            memory["clear_count"] = 0
        elif incident:
            if incident["condition_state"] == "unknown":
                memory["clear_count"] = 0
            memory["clear_count"] = memory.get("clear_count", 0) + 1
            incident["condition_state"] = "active"
            incident["details"].update(distance_units=round(distance, 3), exit_samples=memory["clear_count"])
            for identifier in evidence:
                self.service._evidence(incident, identifier)
            if memory["clear_count"] >= 2:
                self.service._restore(db, incident)
            else:
                self.service._save_incident(db, incident)
        else:
            memory["clear_count"] = 0
        memory["last_checked_time"] = common
        self._save(db, memory_key, memory)

    def tick(self, db):
        if not self.enabled:
            return
        for incident in self.service._incidents(db):
            if incident["type"] not in ("route_deviation", "collision") or incident["condition_state"] == "restored":
                continue
            identifiers = [incident["asset_id"]] + ([incident["other_asset_id"]] if incident["type"] == "collision" else [])
            if not all(self._fresh(self.service._load(db, "asset_state", "asset_id", identifier)) for identifier in identifiers):
                self._unknown(db, incident)
                if incident["type"] == "collision":
                    memory_key = "safety-pair:" + ":".join(sorted(identifiers))
                    memory = self._metadata(db, memory_key)
                    memory["clear_count"] = 0
                    self._save(db, memory_key, memory)
