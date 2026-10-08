import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

FEATURE_VERSION = "movement-v2"
FEATURE_NAMES = ("speed_mean", "speed_std", "idle_ratio", "path_length",
                 "max_step_distance", "stop_start_count", "mean_direction_change",
                 "significant_reversals_30", "interior_reversals_30",
                 "net_progress_ratio_30", "motion_range_30", "moving_fraction_30",
                 "road_end_distance_mean_10")
WINDOW_SECONDS = 30
SHORT_WINDOW_SECONDS = 10
CONTEXT_MIN_SECONDS = 28
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


def extract_window(events, asset_id, window_end, context=None):
    """30s measured context and 10s dynamics; no labels, IDs or rule outputs as features."""
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
    if (len(ordered) < MIN_SAMPLES or
            (ordered[-1][0] - ordered[0][0]).total_seconds() < CONTEXT_MIN_SECONDS):
        return FeatureWindow(asset_id, iso(start), iso(end), None, evidence)
    context_points = np.array([sample[1] for _, sample in ordered], dtype=float)
    context_dt = np.array([(b[0] - a[0]).total_seconds() for a, b in zip(ordered, ordered[1:])])
    context_vectors = np.diff(context_points, axis=0)
    context_distances = np.linalg.norm(context_vectors, axis=1)
    significant = context_distances > np.maximum(.04, STATIONARY_SPEED * context_dt)
    endpoints = np.array((context or {}).get("road_endpoints", []), dtype=float)
    reversals, interior = 0, 0
    indices = np.flatnonzero(significant)
    for first, second in zip(indices, indices[1:]):
        a, b = context_vectors[first], context_vectors[second]
        if float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))) < -.8660254:
            reversals += 1
            # A sampled reversal may miss the exact end by up to a sample step.
            # This separates normal end-of-access turns from reversals inside a road.
            radius = max(.5, (context_distances[first] + context_distances[second]) / 2)
            turning_points = context_points[first + 1:second + 1]
            if len(endpoints) and len(turning_points):
                distance = float(np.linalg.norm(turning_points[:, None] - endpoints, axis=2).min())
                interior += distance > radius
    short = [row for row in ordered if row[0] >= end - timedelta(seconds=SHORT_WINDOW_SECONDS)]
    if len(short) < MIN_SAMPLES:
        return FeatureWindow(asset_id, iso(start), iso(end), None, evidence)
    points = np.array([sample[1] for _, sample in short], dtype=float)
    dt = np.array([(b[0] - a[0]).total_seconds() for a, b in zip(short, short[1:])])
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
                float(np.mean(angles)) if angles else 0.0,
                float(reversals), float(interior),
                float(np.linalg.norm(context_points[-1] - context_points[0]) / max(context_distances.sum(), 1e-9)),
                float(np.linalg.norm(np.ptp(context_points, axis=0))), float(significant.mean()),
                float(np.linalg.norm(points[:, None] - endpoints, axis=2).min(axis=1).mean()) if len(endpoints) else 0.0)
    return FeatureWindow(asset_id, iso(start), iso(end), features, evidence)
