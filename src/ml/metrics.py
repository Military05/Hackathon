import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score


def baseline_scores(x):
    """Interpretable irregularity baseline, independent of labels and zone rules."""
    return x[:, 1] + 0.25 * x[:, 5] + 0.5 * x[:, 6]


def choose_threshold(y, scores):
    candidates = np.unique(np.r_[0., scores, np.nextafter(scores.max(), np.inf)])
    # Pick on validation only. Resolve equal F1 by precision then the higher threshold.
    return float(max(candidates, key=lambda t: (f1_score(y, scores >= t, zero_division=0),
                                                precision_score(y, scores >= t, zero_division=0), t)))


def report(y, scores, threshold, rows):
    predicted = scores >= threshold
    groups = {}
    false_incidents = 0
    for row, positive in zip(rows, predicted):
        group = groups.setdefault(row["episode_id"], {"anomaly": False, "detected": False,
                                                      "false_episode": False, "latency": None,
                                                      "active": False, "normal_windows": 0})
        group["anomaly"] |= row["label"] == 1
        if positive:
            if row["label"] == 0:
                group["false_episode"] = True
                if not group["active"]:
                    false_incidents += 1
            else:
                group["detected"] = True
                if group["latency"] is None:
                    from .features import parse_time
                    group["latency"] = (parse_time(row["window_end"]) - parse_time(row["anomaly_onset"])).total_seconds()
            group["active"], group["normal_windows"] = True, 0
        else:
            group["normal_windows"] += 1
            if group["normal_windows"] >= 2:
                group["active"] = False
    latencies = [g["latency"] for g in groups.values() if g["latency"] is not None]
    return {"threshold": threshold, "windows": len(y), "precision": float(precision_score(y, predicted, zero_division=0)),
            "recall": float(recall_score(y, predicted, zero_division=0)), "f1": float(f1_score(y, predicted, zero_division=0)),
            "confusion_matrix": confusion_matrix(y, predicted, labels=[0, 1]).tolist(),
            "confusion_order": ["normal", "anomaly"], "episodes": len(groups),
            "anomaly_episodes": sum(g["anomaly"] for g in groups.values()),
            "detected_anomaly_episodes": sum(g["detected"] for g in groups.values()),
            "episodes_with_false_alarm": sum(g["false_episode"] for g in groups.values()),
            "false_incidents": false_incidents, "missed_anomaly_episodes": sum(g["anomaly"] and not g["detected"] for g in groups.values()),
            "latency_seconds_median": float(np.median(latencies)) if latencies else None,
            "latency_seconds_max": float(max(latencies)) if latencies else None,
            "latency_scope": "event-time onset to first positive homogeneous window; includes 10s window accumulation"}
