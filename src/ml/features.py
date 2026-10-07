import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

FEATURE_VERSION = "movement-v1"
FEATURE_NAMES = ("speed_mean", "speed_std", "idle_ratio", "path_length",
                 "max_step_distance", "stop_start_count", "mean_direction_change")
WINDOW_SECONDS = 10
STEP_SECONDS = 5
MIN_SAMPLES = 6
STATIONARY_SPEED = 0.05


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class FeatureWindow:
    asset_id: str
    window_start: str
    window_end: str
    features: tuple | None
    evidence_event_ids: tuple


def extract_window(events, asset_id, window_end):
    """Closed [end-10s, end] window; lexicographically last ID wins a timestamp tie."""
    end = parse_time(window_end)
    start = end - timedelta(seconds=WINDOW_SECONDS)
    samples, seen = {}, {}
    for event in events:
        if event.get("type") != "position" or event.get("payload", {}).get("asset_id") != asset_id:
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Position needs an event_id")
        stamp = parse_time(event.get("event_time"))
        payload = event["payload"]
        coords = (payload.get("x"), payload.get("y"))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or not 0 <= v <= 100 for v in coords):
            raise ValueError("Position coordinates must be finite numbers in [0,100]")
        identity = (stamp, *coords)
        if event_id in seen and seen[event_id] != identity:
            raise ValueError("Conflicting duplicate event_id")
        seen[event_id] = identity
        if start <= stamp <= end:
            previous = samples.get(stamp)
            if previous is None or event_id > previous[0]:
                samples[stamp] = (event_id, coords)
    ordered = sorted(samples.items())
    evidence = tuple(sample[0] for _, sample in ordered)
    if len(ordered) < MIN_SAMPLES:
        return FeatureWindow(asset_id, iso(start), iso(end), None, evidence)
    points = np.array([sample[1] for _, sample in ordered], dtype=float)
    dt = np.array([(b[0] - a[0]).total_seconds() for a, b in zip(ordered, ordered[1:])])
    vectors = np.diff(points, axis=0)
    distances = np.linalg.norm(vectors, axis=1)
    speeds = distances / dt
    stationary = speeds <= STATIONARY_SPEED
    nonzero = vectors[distances > 0]
    angles = []
    for a, b in zip(nonzero, nonzero[1:]):
        cosine = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
        angles.append(math.acos(float(np.clip(cosine, -1, 1))))
    features = (float(speeds.mean()), float(speeds.std(ddof=0)), float(stationary.mean()),
                float(distances.sum()), float(distances.max()),
                float(np.count_nonzero(stationary[1:] != stationary[:-1])),
                float(np.mean(angles)) if angles else 0.0)
    return FeatureWindow(asset_id, iso(start), iso(end), features, evidence)
