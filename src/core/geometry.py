"""Plan geometry adapted from Traccar v6.5 GeofencePolygon.containsPoint.

Upstream: https://github.com/traccar/traccar/blob/b89a69251bded7036488c765ec17499cb5e88346/src/main/java/org/traccar/geofence/GeofencePolygon.java
Copyright 2016 - 2020 Anton Tananaev (anton@traccar.org).
Copyright © 2016-2024 The Thingsboard Authors (AlarmStatus projection below).
Apache License 2.0; see third_party/references for original sources and notices.
This port uses x/y plan coordinates and deliberately includes polygon boundaries.
"""
import math


def contains_point(x, y, points):
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        ax, ay = previous
        bx, by = current
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if math.isclose(cross, 0, abs_tol=1e-9) and min(ax, bx) - 1e-9 <= x <= max(ax, bx) + 1e-9 and min(ay, by) - 1e-9 <= y <= max(ay, by) + 1e-9:
            return True
        if (ay > y) != (by > y):
            crossing_x = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def rectangle_contains(x, y, rectangle):
    left, top = rectangle["x"], rectangle["y"]
    right, bottom = left + rectangle["width"], top + rectangle["height"]
    return contains_point(x, y, [(left, top), (right, top), (right, bottom), (left, bottom)])


def alarm_state(incident):
    """ThingsBoard v3.9.1 AlarmStatus.java four-state read-only projection.

    Apache 2.0, ThingsBoard contributors; exact reference in third_party/references.
    v2 status and ownership remain independent from whether a condition is active.
    """
    condition = "ACTIVE" if incident["condition_active"] else "CLEARED"
    acknowledgement = "ACK" if incident.get("assigned_operator_id") is not None else "UNACK"
    return condition + "_" + acknowledgement
