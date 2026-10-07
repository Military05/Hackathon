import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.ml.features import FEATURE_NAMES, extract_window, iso
from src.ml.inference import AnomalyState, MovementModel, WindowEvaluator

END = datetime(2026, 10, 7, 9, 0, 10, tzinfo=timezone.utc)


def positions(coords):
    return [{"event_id": f"p{i}", "event_time": iso(END - timedelta(seconds=10 - i * 2)),
             "type": "position", "payload": {"asset_id": "V1", "x": x, "y": y}, "demo": True}
            for i, (x, y) in enumerate(coords)]


def test_features_hand_calculated():
    window = extract_window(positions([(0, 0), (2, 0), (2, 0), (2, 2), (4, 2), (4, 2)]), "V1", iso(END))
    assert len(FEATURE_NAMES) == 7
    assert window.features == pytest.approx((.6, math.sqrt(.24), .4, 6, 2, 3, math.pi / 2))


def test_timestamp_ties_and_event_order_are_deterministic():
    events = positions([(i, 0) for i in range(6)])
    duplicate = dict(events[1], event_id="zz", payload={"asset_id": "V1", "x": 1, "y": 0})
    a = extract_window(events + [duplicate], "V1", iso(END))
    b = extract_window(list(reversed(events + [duplicate])), "V1", iso(END))
    assert a == b
    assert "zz" in a.evidence_event_ids and "p1" not in a.evidence_event_ids


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 101, True, "1"])
def test_invalid_coordinates(value):
    events = positions([(i, 0) for i in range(6)])
    events[0]["payload"]["x"] = value
    with pytest.raises(ValueError):
        extract_window(events, "V1", iso(END))


def test_missing_data_or_artifact_is_not_normal(tmp_path):
    window = extract_window(positions([(i, 0) for i in range(5)]), "V1", iso(END))
    assert window.features is None
    model = MovementModel(tmp_path / "missing.joblib")
    result = model.observe([], "V1", iso(END))
    assert result["status"] == "not_trained" and result["score"] is None


def test_lifecycle_recovery_dismissal_and_new_episode():
    state, action = AnomalyState().advance({"status": "anomaly"})
    assert action == "create"
    state = state.dismiss()
    state, action = state.advance({"status": "anomaly"})
    assert state.suppressed and action == "reuse"
    state, _ = state.advance({"status": "normal"})
    state, _ = state.advance({"status": "insufficient_data"})
    assert state.active and state.normal_windows == 0
    state, _ = state.advance({"status": "normal"})
    state, action = state.advance({"status": "normal"})
    assert action == "restore" and not state.active and not state.suppressed
    state, action = state.advance({"status": "anomaly"})
    assert action == "create"


def test_published_window_survives_late_data_and_restored_watermark(tmp_path):
    evaluator = WindowEvaluator(MovementModel(tmp_path / "missing.joblib"))
    result, fresh = evaluator.evaluate([], "V1", iso(END))
    assert fresh
    restored = WindowEvaluator(evaluator.model, {"V1": result})
    same, fresh = restored.evaluate(positions([(i, 0) for i in range(6)]), "V1", iso(END))
    assert not fresh and same == result
    with pytest.raises(ValueError):
        restored.evaluate([], "V1", iso(END + timedelta(seconds=1)))
