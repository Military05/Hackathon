import argparse
import hashlib
import json
import platform
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .dataset import matrix, read_episodes
from .features import FEATURE_NAMES, FEATURE_VERSION
from .metrics import baseline_scores, choose_threshold, report


def code_hash():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def train(input_directory, output_directory):
    dataset_hash = hashlib.sha256((Path(input_directory) / "episodes.jsonl").read_bytes()).hexdigest()
    manifest_path = Path(input_directory) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if manifest is not None and manifest.get("dataset_sha256") != dataset_hash:
        raise ValueError("Dataset hash does not match its manifest")
    episodes = read_episodes(input_directory)
    x, y, _ = matrix(episodes, "train")
    vx, vy, vrows = matrix(episodes, "validation")
    model = Pipeline([("scaler", StandardScaler()),
                      ("mlp", MLPClassifier(hidden_layer_sizes=(16, 8), random_state=42,
                                            max_iter=600, early_stopping=False))])
    started = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model.fit(x, y)
    elapsed = time.perf_counter() - started
    column = list(model.classes_).index(1)
    scores = model.predict_proba(vx)[:, column]
    threshold = choose_threshold(vy, scores)
    baseline_threshold = choose_threshold(vy, baseline_scores(vx))
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / "movement.joblib"
    joblib.dump(model, artifact)
    metadata = {"model_version": f"movement-mlp-v1-{dataset_hash[:12]}", "feature_version": FEATURE_VERSION,
                "feature_names": list(FEATURE_NAMES), "classes": model.classes_.tolist(), "positive_class": 1,
                "threshold": threshold, "baseline_threshold": baseline_threshold,
                "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "dataset_sha256": dataset_hash, "code_sha256": code_hash(),
                "versions": {"python": platform.python_version(), "numpy": np.__version__,
                             "scikit_learn": sklearn.__version__, "joblib": joblib.__version__},
                "split_episode_ids": {split: [e["episode_id"] for e in episodes if e["split"] == split]
                                      for split in ("train", "validation", "test")},
                "training": {"windows": len(y), "hidden_layer_sizes": [16, 8], "random_state": 42,
                             "max_iter": 600, "iterations": model["mlp"].n_iter_, "seconds": elapsed,
                             "warnings": [str(w.message) for w in caught], "scaler_fit_split": "train",
                             "threshold_fit_split": "validation", "test_used_for_training": False},
                "validation": {"mlp": report(vy, scores, threshold, vrows),
                               "baseline": report(vy, baseline_scores(vx), baseline_threshold, vrows)},
                "normal_recovery_windows": 2, "window_seconds": 10, "step_seconds": 5, "min_samples": 6,
                "scope": "demo synthetic unusual movement, not collision probability"}
    if manifest is not None:
        metadata["dataset_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        metadata["dataset_generator"] = manifest.get("generator", "legacy-road-v1")
        for name in ("site_sha256", "site_semantic_sha256", "layout_version", "policy_version", "coverage", "seeds"):
            if name in manifest:
                metadata[name] = manifest[name]
    (output / "movement.metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/generated")
    parser.add_argument("--output", default="artifacts/local")
    args = parser.parse_args()
    metadata = train(args.input, args.output)
    print(json.dumps({"model_version": metadata["model_version"], "training": metadata["training"],
                      "validation": metadata["validation"]}, indent=2))


if __name__ == "__main__":
    main()
