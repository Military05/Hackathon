"""Adapter for the v5 scheduler; inference uses the original movement-v1 Pipeline."""
import json
import os
import threading
from pathlib import Path

from .features import iso, parse_time
from .inference import MovementModel as PipelineModel

ROOT = Path(__file__).resolve().parents[2]


class MovementModel(PipelineModel):
    def __init__(self, service=None, artifact=None):
        super().__init__(artifact or os.environ.get(
            "DISPATCH_ML_ARTIFACT", str(ROOT / "models/movement-v1/movement.joblib")))
        self.service = service
        self.lock = threading.Lock()

    def health(self):
        return {"status": self.state, "model_version": self.metadata.get("model_version"),
                "feature_version": "movement-v1", "error": self.error}

    def evaluate(self, asset_id, events, window_end):
        end = parse_time(window_end)
        end = end.fromtimestamp(int(end.timestamp()) // 5 * 5, tz=end.tzinfo)
        with self.lock:
            # Use the committed observation as the watermark. Never advance it before
            # Service atomically saves the observation and its incident effect.
            if self.service:
                with self.service.store.read() as db:
                    row = db.execute("SELECT body FROM model_observations WHERE asset_id=? "
                                     "ORDER BY window_end DESC,observation_id DESC LIMIT 1", (asset_id,)).fetchone()
                    if row:
                        previous = json.loads(row[0])
                        if parse_time(previous["window_end"]) >= end:
                            return previous
            return self.observe(events, asset_id, iso(end))
