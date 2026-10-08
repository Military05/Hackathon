"""Map-derived synthetic episodes; permissions and collision are not ML labels."""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from src.core.demo_traffic import ArcRoute, Journey
from .features import iso

ROOT = Path(__file__).resolve().parents[2]


def route_catalog(site):
    routes = {}
    for asset, points in site["demo_routes"].items():
        routes["personal:" + asset] = {"points": points, "road_ids": [], "building_ids": []}
    for road in site["roads"]:
        points = road["points"]
        # Travel to the end and back: an ordinary reversal at a loading entrance.
        loop = points if points[0] == points[-1] else points + list(reversed(points[:-1]))
        routes["road:" + road["id"]] = {
            "points": loop, "road_ids": [road["id"]],
            "building_ids": [building["id"] for building in site["buildings"]
                             if building.get("entrance", {}).get("road_id") == road["id"]]}
        ArcRoute(loop)  # Validate geometry before writing any dataset.
    return routes


def make_episode(rng, site, route_id, definition, asset, split, index, label):
    episode_id = f"factory-v7-{split}-{asset['id']}-{index:05d}-{label}"
    route = ArcRoute(definition["points"])
    normal_kind = ("transit", "loading", "waiting", "turns")[(index // 2) % 4]
    interval = float(rng.uniform(.65, 1.35) if split == "test" else rng.uniform(.8, 1.1))
    noise = float(rng.uniform(0, .009) if split == "test" else rng.uniform(0, .006))
    speed = float(rng.uniform(.65, 2.45))
    pause = float(rng.uniform(3, 12)) if normal_kind in ("loading", "waiting") else 0.
    journey = Journey(route, float(rng.uniform(0, route.length)), speed, pause,
                      phase=float(rng.uniform(0, route.length / speed + pause)))
    sensor = next(sensor["id"] for sensor in site["sensors"]
                  if sensor["type"] == "position" and sensor.get("asset_id") == asset["id"])
    onset = 30. if label else None
    start = datetime(2026, 10, 8, 9, tzinfo=timezone.utc) + timedelta(minutes=index * 3)
    pattern = ("bursts", "stop_start", "oscillation", "slow_oscillation", "wall_push")[index % 5] if label else normal_kind
    long_segments = [(np.array(a, dtype=float), np.array(b, dtype=float))
                     for road in site["roads"] for a, b in zip(road["points"], road["points"][1:])
                     if np.linalg.norm(np.array(a) - b) > 20]
    segment_start, segment_end = long_segments[int(rng.integers(len(long_segments)))]
    axis = (segment_end - segment_start) / np.linalg.norm(segment_end - segment_start)
    oscillation_center = segment_start + (segment_end - segment_start) * float(rng.uniform(.35, .65))
    oscillation_period, oscillation_amplitude = float(rng.uniform(10, 17)), float(rng.uniform(3.5, 6.5))
    building = site["buildings"][int(rng.integers(len(site["buildings"]))) ]
    entrance = building["entrance"]
    bounds = building["rectangle"]
    wall_anchor = np.array([entrance["x"], entrance["y"]], dtype=float)
    outward = wall_anchor - np.array([bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2])
    outward = outward / np.linalg.norm(outward)
    push_period, push_amplitude = float(rng.uniform(7, 11)), float(rng.uniform(.4, .9))
    events, elapsed, motion_time = [], 0., 0.
    point = np.array(journey.position(0))
    anomaly_anchor = None
    while elapsed <= 120:
        delta = interval * float(rng.uniform(.92, 1.08))
        if onset is None or elapsed < onset:
            # Smooth variation is ordinary; stopping/starting is not an anomaly alone.
            advance = delta * float(rng.uniform(.96, 1.04))
            if normal_kind == "waiting" and 12 <= elapsed <= 28:
                advance = 0.
            motion_time += advance
            point = np.array(journey.position(motion_time))
        else:
            if anomaly_anchor is None:
                anomaly_anchor = point.copy()
            if pattern == "slow_oscillation":
                phase = ((elapsed - onset) / oscillation_period) % 1
                point = oscillation_center + axis * oscillation_amplitude * (1 - 4 * abs(phase - .5))
            elif pattern == "wall_push":
                phase = ((elapsed - onset) / push_period) % 1
                # Repeated small approaches/retreats separated by pauses, not a
                # stationary loading point. Model sees measured positions only.
                excursion = max(0., 1 - abs(phase - .65) / .25)
                point = wall_anchor + outward * (.2 + push_amplitude * excursion)
            elif pattern == "oscillation":
                point = anomaly_anchor + np.array([5 * np.sin(elapsed * 2.3), 4 * np.cos(elapsed * 1.9)])
            else:
                moving = rng.random() > (.35 if pattern == "stop_start" else .1)
                if moving:
                    angle = float(rng.uniform(0, 2 * np.pi))
                    distance = float(rng.uniform(3.8, 7.5)) * delta
                    point = point + distance * np.array([np.cos(angle), np.sin(angle)])
                    # Keep anomalous measurements local to the same current map object.
                    point = np.clip(point, anomaly_anchor - 12, anomaly_anchor + 12)
        measured = np.clip(point + rng.normal(0, noise, 2), 0, 100)
        events.append({"event_id": f"{episode_id}-p{len(events):04d}",
                       "event_time": iso(start + timedelta(seconds=elapsed)),
                       "sensor_id": sensor, "type": "position", "demo": True,
                       "payload": {"asset_id": asset["id"], "x": round(float(measured[0]), 6),
                                   "y": round(float(measured[1]), 6)}})
        elapsed += delta
    return {"episode_id": episode_id, "split": split, "label": label,
            "scenario": pattern, "vehicle_type": asset["vehicle_type"], "asset_id": asset["id"],
            "route_id": route_id, "road_ids": definition["road_ids"],
            "building_ids": definition["building_ids"], "sample_interval": interval,
            "noise_std": noise, "anomaly_onset": iso(start + timedelta(seconds=onset)) if onset else None,
            "events": events}


def generate_factory(output, site_path=ROOT / "data/demo/site.json", seed=20261018,
                     validation_seed=20261019, test_seed=20261020, repeats=(3, 1, 1)):
    seeds = dict(zip(("train", "validation", "test"), (seed, validation_seed, test_seed)))
    if len(set(seeds.values())) != 3 or len(repeats) != 3 or min(repeats) < 1:
        raise ValueError("Три разных seed и положительное число повторов обязательны")
    site_path = Path(site_path)
    site = json.loads(site_path.read_text(encoding="utf-8"))
    routes = route_catalog(site)
    assets = [asset for asset in site["assets"] if asset["type"] == "vehicle"]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    content, counts = [], {}
    for split, repetitions in zip(seeds, repeats):
        rng = np.random.default_rng(seeds[split])
        count = 0
        for repeat in range(repetitions):
            for route_id, definition in routes.items():
                for asset in assets:
                    for label in (0, 1):
                        episode = make_episode(rng, site, route_id, definition, asset, split, count, label)
                        content.append(json.dumps(episode, ensure_ascii=False, sort_keys=True) + "\n")
                        count += 1
        counts[split] = count
    # Explicit UTF-8/newlines make byte hashes independent of Windows text defaults.
    serialized = "".join(content).encode("utf-8")
    (output / "episodes.jsonl").write_bytes(serialized)
    manifest = {"schema": "movement-episodes-v1", "generator": "factory-map-v7",
                "feature_context": {"road_endpoints": sorted({tuple(point) for road in site["roads"]
                    for point in (road["points"][0], road["points"][-1])})},
                "anomaly_cases": ["bursts", "stop_start", "oscillation", "slow_oscillation", "wall_push"],
                "seeds": seeds, "repeats": list(repeats), "episode_counts": counts,
                "dataset_sha256": hashlib.sha256(serialized).hexdigest(),
                "site_sha256": hashlib.sha256(site_path.read_bytes()).hexdigest(),
                "site_semantic_sha256": hashlib.sha256(json.dumps(site, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")).hexdigest(),
                "layout_version": site["layout_version"], "policy_version": site["policy_version"],
                "coverage": {"building_ids": [b["id"] for b in site["buildings"]],
                             "road_ids": [r["id"] for r in site["roads"]],
                             "zone_ids": [z["id"] for z in site["zones"]],
                             "asset_ids": [a["id"] for a in assets],
                             "employee_ids": [a["id"] for a in site["assets"] if a["type"] == "employee"],
                             "sensor_ids": [s["id"] for s in site["sensors"]]},
                "route_catalog": routes,
                "normal_cases": ["transit", "loading", "waiting", "turns", "entrance_reversal"],
                "test_shift": "independent seed, wider intervals and measurement noise",
                "scope": "synthetic unusual single-vehicle movement; zone/access/collision labels excluded"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default=str(ROOT / "data/demo/site.json"))
    parser.add_argument("--output", default="data/generated/factory-v6")
    args = parser.parse_args()
    manifest = generate_factory(args.output, args.site)
    print(json.dumps({key: manifest[key] for key in ("episode_counts", "dataset_sha256", "site_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
