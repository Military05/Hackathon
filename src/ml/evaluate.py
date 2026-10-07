import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np

from .dataset import episode_windows, matrix, read_episodes
from .inference import MovementModel
from .metrics import baseline_scores, report


def evaluate(input_directory, artifact):
    episodes = read_episodes(input_directory)
    model = MovementModel(artifact)
    if model.state != "ready":
        raise ValueError(f"Model unavailable: {model.state}: {model.error}")
    metadata = model.metadata
    if hashlib.sha256((Path(input_directory) / "episodes.jsonl").read_bytes()).hexdigest() != metadata["dataset_sha256"]:
        raise ValueError("Dataset differs from recorded train/validation/test split")
    actual = {split: [e["episode_id"] for e in episodes if e["split"] == split]
              for split in ("train", "validation", "test")}
    if actual != metadata["split_episode_ids"]:
        raise ValueError("Split changed after training")
    x, y, rows = matrix(episodes, "test")
    column = list(model.pipeline.classes_).index(1)
    scores = model.pipeline.predict_proba(x)[:, column]
    # Serving measurement includes extractor + one-window inference, not just batch scoring.
    timings = []
    for episode in [e for e in episodes if e["split"] == "test"][:8]:
        for window, label in episode_windows(episode):
            if label is None:
                continue
            started = time.perf_counter()
            model.observe(episode["events"], episode["asset_id"], window.window_end)
            timings.append((time.perf_counter() - started) * 1000)
    result = {"model_version": metadata["model_version"], "dataset_sha256": metadata["dataset_sha256"],
              "artifact_sha256": metadata["artifact_sha256"], "test_used_for_threshold": False,
              "mlp": report(y, scores, metadata["threshold"], rows),
              "baseline": report(y, baseline_scores(x), metadata["baseline_threshold"], rows),
              "inference_milliseconds": {"samples": len(timings), "median": float(np.median(timings)),
                                         "p95": float(np.percentile(timings, 95)), "max": float(max(timings)),
                                         "machine": platform.platform(),
                                         "scope": "local CPU single-window measurement; not a concurrent-load benchmark"}}
    Path(artifact).with_name("evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/generated")
    parser.add_argument("--model", default="artifacts/local/movement.joblib")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.input, args.model), indent=2))


if __name__ == "__main__":
    main()
