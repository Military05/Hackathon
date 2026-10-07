"""Export an independent D4 episode, observations and a B1 snapshot using trained inference."""
import argparse
import json
from pathlib import Path

from .dataset import episode_windows, read_episodes
from .features import parse_time
from .inference import MovementModel


def export_demo(input_directory, artifact, output_directory):
    model = MovementModel(artifact)
    if model.state != "ready":
        raise ValueError(f"Cannot export D4 without a valid trained model: {model.state}")
    episode = next(e for e in read_episodes(input_directory) if e["split"] == "test" and e["label"] == 1)
    observations = [model.observe(episode["events"], episode["asset_id"], w.window_end)
                    for w, label in episode_windows(episode) if label is not None]
    anomaly = next(o for o in observations if o["status"] == "anomaly")
    events = [e for e in episode["events"] if e["event_id"] in anomaly["evidence_event_ids"]]
    snapshot = {"as_of": anomaly["window_end"], "rule_version": "fixture-rules-v2.1",
                "model_version": model.metadata["model_version"],
                "incident": {"incident_id": "INC-MODEL-D4", "type": "model_anomaly", "asset_id": "V1",
                             "sensor_id": "POS-V1", "condition_active": True, "condition_state": "active",
                             "evidence_event_ids": anomaly["evidence_event_ids"], "observation_id": anomaly["observation_id"],
                             "details": {"observation_id": anomaly["observation_id"],
                                         "score": anomaly["score"], "threshold": anomaly["threshold"]},
                             "score": anomaly["score"], "threshold": anomaly["threshold"], "demo": True},
                "events": events, "observations": [anomaly],
                "policies": {"V1": {"asset_id": "V1", "type": "vehicle", "vehicle_type": episode["vehicle_type"],
                                    "allowed_buildings": ["W1", "W2"], "policy_version": "fixture-policy-v1"}},
                "sensor_health": {"POS-V1": {"sensor_id": "POS-V1", "status": "online",
                    "last_received_at": events[-1]["event_time"], "threshold_seconds": 5, "version": "sensor-health-v1"}}}
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("d4-episode.json", episode), ("d4-observations.json", observations), ("d4-agent-snapshot.json", snapshot)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return {"episode_id": episode["episode_id"], "observations": len(observations),
            "first_anomaly": anomaly, "backend_incident_created": False, "scope": "explicit D4 fixture"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/generated")
    parser.add_argument("--model", default="artifacts/local/movement.joblib")
    parser.add_argument("--output", default="artifacts/local")
    args = parser.parse_args()
    print(json.dumps(export_demo(args.input, args.model, args.output), indent=2))


if __name__ == "__main__":
    main()
