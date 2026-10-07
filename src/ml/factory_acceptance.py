"""Replay actual factory Events in isolated SQLite; no fixture model responses."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time

import numpy as np

from src.core.demo_traffic import DemoRunner, SCENARIOS
from src.core.service import Service, stamp
from .inference import MovementModel as PreviousModel
from .movement import MovementModel

ROOT = Path(__file__).resolve().parents[2]
NORMAL_SCENARIOS = {"normal", "logistics", "service", "shift", "safe-passing"}


def replay_factory(artifact, site_path=ROOT / "data/demo/site.json", seconds=180,
                   scenarios=None, compare_artifact=None):
    if seconds < 15:
        raise ValueError("Нужно не менее 15 секунд модельного времени")
    results, timings = {}, []
    previous = PreviousModel(compare_artifact) if compare_artifact else None
    if previous and previous.state != "ready":
        raise ValueError("Предыдущая модель недоступна для сравнения")
    with tempfile.TemporaryDirectory(prefix="factory-ml-acceptance-") as directory:
        for scenario in scenarios or SCENARIOS:
            moment = [datetime(2026, 10, 8, 9, tzinfo=timezone.utc)]
            service = Service(Path(directory) / (scenario + ".db"), site_path, clock=lambda: moment[0])
            model = MovementModel(service, artifact)
            if model.state != "ready":
                raise ValueError(f"Модель не готова: {model.error}")
            service.model_version = model.metadata["model_version"]
            source = DemoRunner(service)
            source._prepare(scenario)
            windows, old_windows = [], []
            for elapsed in range(seconds):
                source.emit_frame(elapsed)
                service.tick()
                if elapsed % 5 == 0:
                    for asset in (a for a in service.site["assets"] if a["type"] == "vehicle"):
                        events = service.event_history(asset["id"], stamp(moment[0] - timedelta(seconds=10)), stamp(moment[0]), 100)
                        started = time.perf_counter()
                        result = model.evaluate(asset["id"], events, stamp(moment[0]))
                        timings.append((time.perf_counter() - started) * 1000)
                        service.register_model_observation(result)
                        windows.append(result)
                        if previous:
                            old_windows.append(previous.observe(events, asset["id"], stamp(moment[0])))
                moment[0] += timedelta(seconds=1)
            counts = Counter(incident["type"] for incident in service.list_incidents())
            with service.store.read() as db:
                event_count = db.execute("SELECT count(*) FROM events").fetchone()[0]
            results[scenario] = {
                "events": event_count,
                "windows": len(windows), "states": dict(Counter(window["status"] for window in windows)),
                "model_incidents": counts.get("model_anomaly", 0), "all_incident_types": dict(counts),
                "max_score": max(window["score"] or 0 for window in windows),
                "normal_motion_acceptance": scenario in NORMAL_SCENARIOS,
                "anomalies_by_asset": dict(Counter(window["asset_id"] for window in windows if window["status"] == "anomaly")),
                "previous_anomaly_windows": sum(window["status"] == "anomaly" for window in old_windows) if previous else None}
    return {"model_version": model.metadata["model_version"], "artifact_sha256": model.metadata["artifact_sha256"],
            "site_sha256": hashlib.sha256(Path(site_path).read_bytes()).hexdigest(),
            "seconds_per_scenario": seconds, "scenarios": results,
            "normal_model_incidents": sum(result["model_incidents"] for result in results.values() if result["normal_motion_acceptance"]),
            "adapter_milliseconds": {"samples": len(timings), "median": float(np.median(timings)),
                                     "p95": float(np.percentile(timings, 95)), "max": float(max(timings))},
            "execution": "actual DemoRunner + Service + SQLite + trained MLP; event time accelerated, no LLM",
            "limits": "synthetic replay, not a wall-clock load benchmark or industrial ground truth"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="models/movement-v1/movement.joblib")
    parser.add_argument("--compare-model")
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--output", default="artifacts/local/factory-acceptance.json")
    args = parser.parse_args()
    report = replay_factory(args.model, seconds=args.seconds, compare_artifact=args.compare_model)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
