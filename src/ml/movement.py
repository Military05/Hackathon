"""Bounded movement-v2 history adapter; one trained CPU Pipeline at startup."""
import json
import hashlib
import os
import threading
from pathlib import Path

from datetime import timedelta
from .features import FEATURE_VERSION, WINDOW_SECONDS, iso, parse_time
from .inference import MovementModel as PipelineModel

ROOT = Path(__file__).resolve().parents[2]


class MovementModel(PipelineModel):
    def __init__(self, service=None, artifact=None):
        super().__init__(artifact or os.environ.get(
            "DISPATCH_ML_ARTIFACT", str(ROOT / "models/movement-v1/movement.joblib")))
        self.service = service
        self.lock = threading.Lock()
        expected_site = self.metadata.get("site_semantic_sha256")
        if service and expected_site and self.state == "ready":
            actual_site = hashlib.sha256(json.dumps(service.site, sort_keys=True, ensure_ascii=False,
                separators=(",", ":")).encode("utf-8")).hexdigest()
            if actual_site != expected_site:
                self.pipeline = None
                self.state = "artifact_invalid"
                self.error = "Карта отличается от обучающего набора; требуется новая проверка и обучение MLP"

    def health(self):
        return {"status": self.state, "model_version": self.metadata.get("model_version"),
                "feature_version": FEATURE_VERSION, "error": self.error}

    def evaluate(self, asset_id, events, window_end):
        end = parse_time(window_end)
        end = end.fromtimestamp(int(end.timestamp()) // 5 * 5, tz=end.tzinfo)
        with self.lock:
            # Use the committed observation as the watermark. Never advance it before
            # Service atomically saves the observation and its incident effect.
            if self.service and self.state == "ready":
                with self.service.store.read() as db:
                    row = db.execute("SELECT body FROM model_observations WHERE asset_id=? "
                                     "AND json_extract(body, '$.model_version')=? "
                                     "ORDER BY window_end DESC,observation_id DESC LIMIT 1",
                                     (asset_id, self.metadata.get("model_version"))).fetchone()
                    if row:
                        previous = json.loads(row[0])
                        if parse_time(previous["window_end"]) >= end:
                            return previous
            if self.service and self.state == "ready":
                # Scheduler's public API still supplies 10s; read bounded measured
                # context through the existing store API, without changing Events.
                events = self.service.event_history(asset_id, iso(end - timedelta(seconds=WINDOW_SECONDS)), iso(end), 100)
            return self.observe(events, asset_id, iso(end))
