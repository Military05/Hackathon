import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from .features import STEP_SECONDS, WINDOW_SECONDS, extract_window, iso, parse_time


def read_episodes(directory):
    episodes = [json.loads(line) for line in (Path(directory) / "episodes.jsonl").read_text().splitlines() if line]
    manifest_path = Path(directory) / "manifest.json"
    if manifest_path.exists():
        context = json.loads(manifest_path.read_text(encoding="utf-8")).get("feature_context")
        if context:
            for episode in episodes:
                episode["feature_context"] = context
    ids = [e["episode_id"] for e in episodes]
    if len(ids) != len(set(ids)):
        raise ValueError("Episode IDs must be unique across all splits")
    if {e["split"] for e in episodes} != {"train", "validation", "test"}:
        raise ValueError("Expected train, validation and test splits")
    return episodes


def episode_windows(episode):
    first = parse_time(episode["events"][0]["event_time"])
    last = parse_time(episode["events"][-1]["event_time"])
    onset = parse_time(episode["anomaly_onset"]) if episode.get("anomaly_onset") else None
    end = first + timedelta(seconds=WINDOW_SECONDS)
    while end <= last:
        window = extract_window(episode["events"], episode["asset_id"], iso(end), episode.get("feature_context"))
        # Transition windows contain mixed ground truth; evaluate only homogeneous windows.
        label = 0 if onset is None or end <= onset else (1 if end - timedelta(seconds=WINDOW_SECONDS) >= onset else None)
        yield window, label
        end += timedelta(seconds=STEP_SECONDS)


def matrix(episodes, split):
    x, y, rows = [], [], []
    for episode in episodes:
        if episode["split"] != split:
            continue
        for window, label in episode_windows(episode):
            if window.features is not None and label is not None:
                x.append(window.features)
                y.append(label)
                rows.append({"episode_id": episode["episode_id"], "window_end": window.window_end,
                             "anomaly_onset": episode.get("anomaly_onset"), "label": label})
    if not x or set(y) != {0, 1}:
        raise ValueError(f"Split {split} must contain both classes with sufficient windows")
    return np.asarray(x), np.asarray(y), rows
