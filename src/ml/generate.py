"""Synthetic movement only: no prohibited-zone or access label shortcuts."""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .features import iso

# A polyline along passages in the current main frontend, outside building interiors.
ROAD = np.array([[20., 27.], [20., 40.], [43., 40.], [43., 72.], [65., 72.]])
LENGTHS = np.linalg.norm(np.diff(ROAD, axis=0), axis=1)
ROAD_LENGTH = float(LENGTHS.sum())


def point_at(distance):
    remaining = float(np.clip(distance, 0, ROAD_LENGTH))
    for a, b, length in zip(ROAD, ROAD[1:], LENGTHS):
        if remaining <= length:
            return a + (b - a) * remaining / length
        remaining -= length
    return ROAD[-1].copy()


def make_episode(rng, episode_id, split, label, start):
    scenario = rng.choice(["transit", "loading", "waiting", "turns"]) if not label else "irregular_motion"
    sample_interval = float(rng.uniform(0.6, 1.3) if split == "test" else rng.uniform(0.8, 1.05))
    noise = float(rng.uniform(0.002, 0.015) if split == "test" else rng.uniform(0.003, 0.01))
    vehicle_type = str(rng.choice(["forklift", "truck", "cart"]))
    base_speed = {"forklift": 0.7, "truck": 1.0, "cart": 0.45}[vehicle_type] * rng.uniform(0.8, 1.2)
    distance = float(rng.uniform(0, 20))
    direction, elapsed, events = 1, 0., []
    pause_start = float(rng.uniform(18, 25))
    pause_end = pause_start + float(rng.uniform(15, 28))
    onset = 20. if label else None
    while elapsed <= 75:
        delta = sample_interval * float(rng.uniform(0.92, 1.08))
        speed = base_speed * float(rng.uniform(0.9, 1.1))
        if scenario in {"loading", "waiting"} and pause_start <= elapsed <= pause_end:
            speed = 0.
        elif scenario == "turns" and int(elapsed) % 25 in {0, 1, 2}:
            speed *= 0.25
        if onset is not None and elapsed >= onset:
            speed = base_speed * float(rng.choice([0., 0., 0.3, 1.5, 2.5, 3.8]))
            if rng.random() < 0.45:
                direction *= -1
        distance += direction * speed * delta
        if distance < 0 or distance > ROAD_LENGTH:
            distance = float(np.clip(distance, 0, ROAD_LENGTH))
            direction *= -1
        x, y = point_at(distance) + rng.normal(0, noise, 2)
        events.append({"event_id": f"{episode_id}-p{len(events):04d}",
                       "event_time": iso(start + timedelta(seconds=elapsed)),
                       "sensor_id": "POS-V1", "type": "position", "demo": True,
                       "payload": {"asset_id": "V1", "x": round(float(x), 6), "y": round(float(y), 6)}})
        elapsed += delta
    return {"episode_id": episode_id, "split": split, "label": label, "scenario": str(scenario),
            "vehicle_type": vehicle_type, "asset_id": "V1", "sample_interval": sample_interval,
            "noise_std": noise, "anomaly_onset": iso(start + timedelta(seconds=onset)) if onset else None,
            "events": events}


def generate(output, seed=42, test_seed=20261007, episodes_per_class=160):
    if seed == test_seed:
        raise ValueError("Independent test requires a different seed")
    if episodes_per_class < 20:
        raise ValueError("At least 20 episodes per class")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rng, test_rng = np.random.default_rng(seed), np.random.default_rng(test_seed)
    episodes = []
    counts = {"train": int(episodes_per_class * .6), "validation": int(episodes_per_class * .2)}
    counts["test"] = episodes_per_class - sum(counts.values())
    start = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
    for split, count in counts.items():
        for label in (0, 1):
            for index in range(count):
                episode_id = f"{split}-{label}-{index:04d}"
                episodes.append(make_episode(test_rng if split == "test" else rng, episode_id, split,
                                             label, start + timedelta(minutes=2 * len(episodes))))
    content = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in episodes)
    (output / "episodes.jsonl").write_bytes(content.encode("utf-8"))
    manifest = {"schema": "movement-episodes-v1", "seed": seed, "test_seed": test_seed,
                "episode_counts": {s: 2 * n for s, n in counts.items()},
                "dataset_sha256": hashlib.sha256(content.encode()).hexdigest(), "road": ROAD.tolist(),
                "test_shift": "different seed, sample interval and position noise",
                "scope": "synthetic unusual movement; no industrial or collision ground truth"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-seed", type=int, default=20261007)
    parser.add_argument("--episodes-per-class", type=int, default=160)
    parser.add_argument("--output", default="data/generated")
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.seed, args.test_seed, args.episodes_per_class), indent=2))


if __name__ == "__main__":
    main()
