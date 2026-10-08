import hashlib
import json
import platform
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import sklearn

from .features import FEATURE_NAMES, FEATURE_VERSION, extract_window, parse_time


class MovementModel:
    """Load a trusted local Pipeline once at startup; never train during serving."""
    def __init__(self, artifact="artifacts/local/movement.joblib"):
        self.pipeline = None
        self.metadata = {}
        self.error = None
        self.artifact = Path(artifact)
        metadata_path = self.artifact.with_suffix(".metadata.json")
        if not self.artifact.exists() and not metadata_path.exists():
            self.state = "not_trained"
            return
        try:
            self.metadata = json.loads(metadata_path.read_text())
            if hashlib.sha256(self.artifact.read_bytes()).hexdigest() != self.metadata["artifact_sha256"]:
                raise ValueError("Artifact hash mismatch")
            if self.metadata["feature_version"] != FEATURE_VERSION or self.metadata["feature_names"] != list(FEATURE_NAMES):
                raise ValueError("Feature version/order mismatch")
            current = {"python": platform.python_version(), "numpy": np.__version__,
                       "scikit_learn": sklearn.__version__, "joblib": joblib.__version__}
            for name in ("numpy", "scikit_learn", "joblib"):
                if self.metadata["versions"][name] != current[name]:
                    raise ValueError(f"Incompatible {name} version; use the pinned environment")
            if self.metadata["versions"]["python"].split(".")[:2] != current["python"].split(".")[:2]:
                raise ValueError("Incompatible Python version")
            threshold = self.metadata["threshold"]
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                raise ValueError("Invalid threshold")
            self.pipeline = joblib.load(self.artifact)
            if list(self.pipeline.classes_) != self.metadata["classes"] or 1 not in self.pipeline.classes_:
                raise ValueError("Class order mismatch")
            if self.pipeline.n_features_in_ != len(FEATURE_NAMES):
                raise ValueError("Feature count mismatch")
            self.state = "ready"
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            self.pipeline = None
            self.state, self.error = "artifact_invalid", str(exc)

    def observe(self, events, asset_id, window_end):
        window = extract_window(events, asset_id, window_end, self.metadata.get("feature_context"))
        version = self.metadata.get("model_version")
        result = {"observation_id": "mo-" + hashlib.sha256(f"{asset_id}|{window.window_end}|{version}".encode()).hexdigest()[:24],
                  "asset_id": asset_id, "window_start": window.window_start, "window_end": window.window_end,
                  "status": self.state, "score": None, "threshold": self.metadata.get("threshold"),
                  "model_version": version, "feature_version": FEATURE_VERSION,
                  "evidence_event_ids": list(window.evidence_event_ids), "demo": True}
        if self.state != "ready":
            if self.error:
                result["error"] = self.error
        elif window.features is None:
            result["status"] = "insufficient_data"
        else:
            column = list(self.pipeline.classes_).index(1)
            result["score"] = float(self.pipeline.predict_proba([window.features])[0, column])
            result["status"] = "anomaly" if result["score"] >= result["threshold"] else "normal"
        return result


class WindowEvaluator:
    """One latest published result per asset. Backend restores this watermark on startup."""
    def __init__(self, model, published=None):
        self.model = model
        self.published = dict(published or {})
        self.lock = threading.Lock()

    def evaluate(self, events, asset_id, window_end):
        end = parse_time(window_end)
        if end.timestamp() % 5 != 0:
            raise ValueError("Published windows must end on the 5-second UTC grid")
        with self.lock:
            previous = self.published.get(asset_id)
            if previous and end <= parse_time(previous["window_end"]):
                return dict(previous), False
            result = self.model.observe(events, asset_id, window_end)
            self.published[asset_id] = result
            return dict(result), True


@dataclass(frozen=True)
class AnomalyState:
    active: bool = False
    normal_windows: int = 0
    suppressed: bool = False

    def dismiss(self):
        if not self.active:
            raise ValueError("Only an active model episode can be dismissed")
        return AnomalyState(True, self.normal_windows, True)

    def advance(self, observation):
        status = observation["status"]
        if status == "anomaly":
            return AnomalyState(True, 0, self.suppressed), "reuse" if self.active else "create"
        if status == "normal":
            count = self.normal_windows + 1 if self.active else 0
            if self.active and count >= 2:
                return AnomalyState(), "restore"
            return AnomalyState(self.active, count, self.suppressed), "none"
        # Missing/unavailable data cannot prove recovery, or count as a normal window.
        return AnomalyState(self.active, 0, self.suppressed), "none"
