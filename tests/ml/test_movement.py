import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.ml.features import FEATURE_NAMES, extract_window, iso
from src.ml.inference import AnomalyState, MovementModel, WindowEvaluator

END = datetime(2026, 10, 7, 9, 0, 10, tzinfo=timezone.utc)


def positions(coords):
    return [{"event_id": f"p{i}", "event_time": iso(END - timedelta(seconds=30 - i * 6)),
             "type": "position", "payload": {"asset_id": "V1", "x": x, "y": y}, "demo": True}
            for i, (x, y) in enumerate(coords)]


def test_features_hand_calculated():
    events = [{"event_id": f"p{i}", "event_time": iso(END - timedelta(seconds=30 - i)),
               "type": "position", "payload": {"asset_id": "V1", "x": i, "y": 0}}
              for i in range(31)]
    window = extract_window(events, "V1", iso(END), {"road_endpoints": [[0, 0], [30, 0]]})
    assert len(FEATURE_NAMES) == 13
    assert window.features == pytest.approx((1, 0, 0, 10, 1, 0, 0, 0, 0, 1, 30, 1, 5))


def test_short_context_does_not_return_a_fabricated_normal_result():
    events = positions([(i, 0) for i in range(6)])
    assert extract_window(events, "V1", iso(END)).features is None


def test_context_reversals_ignore_noise_and_do_not_use_scenario_labels():
    coords = [(30, 30 + min(i % 12, 12 - i % 12)) for i in range(31)]
    events = [{"event_id": f"c{i}", "event_time": iso(END - timedelta(seconds=30 - i)),
               "type": "position", "payload": {"asset_id": "V1", "x": x, "y": y}}
              for i, (x, y) in enumerate(coords)]
    context = {"road_endpoints": [[30, 10], [30, 60]]}
    features = extract_window(events, "V1", iso(END), context).features
    assert features is not None and features[7] >= 4 and features[8] >= 4
    renamed = [dict(event, scenario="normal", label=0) for event in events]
    assert extract_window(renamed, "V1", iso(END), context).features == features
    for event in events:
        event["payload"].update(x=30, y=30)
    parked = extract_window(events, "V1", iso(END), context).features
    assert parked[7] == 0 and parked[8] == 0 and parked[11] == 0


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
