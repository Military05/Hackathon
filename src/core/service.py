"""Demo rules and atomic coordination; no model calls run inside a transaction."""
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import uuid

from src.storage.sqlite_store import Store
from src.core.geometry import rectangle_contains, alarm_state


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or utcnow()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ApiError(422, "invalid_time", "Expected UTC ISO 8601 timestamp", {"value": value})


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class Service:
    rule_version = "rules-v2.1"

    def __init__(self, db_path, site_path, clock=utcnow):
        self.site = json.loads(Path(site_path).read_text(encoding="utf-8"))
        self.clock = clock
        self.started_at = self.clock()
        self.store = Store(db_path)
        self.config = self.site["dispatch_config"]
        self.assets_by_id = {a["id"]: a for a in self.site["assets"]}
        self.sensors_by_id = {s["id"]: s for s in self.site["sensors"]}
        self.areas = {a["id"]: a for a in self.site["site_areas"]}
        self.profiles = {p.get("operator_id") or p.get("id"): p for p in self.site["operator_profiles"]}
        self.buildings = {b["id"]: b for b in self.site["buildings"]}
        self.model_version = "not_loaded"
        with self.store.transaction() as db:
            # Capture already expired leases before invalidating browser sessions.
            # Otherwise a restart between timer cycles erases the start of an absence.
            for operator in self.profiles:
                self._sync_absence(db, operator)
            db.execute("DELETE FROM presence")  # Leases must be renewed after a server restart.
            for sid, sensor in self.sensors_by_id.items():
                state = {"sensor_id": sid, "last_received_at": None, "last_heartbeat_at": None,
                         "last_measurement_at": None, "last_event_id": None,
                         "first_expected_at": stamp(self.started_at), "status": "unknown"}
                db.execute("INSERT OR IGNORE INTO sensor_state VALUES (?,?)", (sid, canonical(state)))
            # Persist absence episodes across restarts; browser leases are intentionally invalidated.
            for operator in self.profiles:
                self._sync_absence(db, operator)

    def _require(self, identifier, mapping, kind):
        if not isinstance(identifier, str) or not identifier:
            raise ApiError(422, "invalid_identifier", kind + " id must be a nonempty string")
        if identifier not in mapping:
            raise ApiError(404, "unknown_" + kind, "Unknown " + kind, {"id": identifier})
        return mapping[identifier]

    def validate_operator(self, operator):
        if not operator:
            raise ApiError(422, "operator_required", "X-Demo-Operator header is required")
        self._require(operator, self.profiles, "operator")
        return operator

    def _load(self, db, table, key, value):
        # Table/key names are internal constants, never model/user arguments.
        row = db.execute(f"SELECT body FROM {table} WHERE {key}=?", (value,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_incident(self, db, incident):
        db.execute("UPDATE incidents SET body=? WHERE incident_id=?",
                   (canonical(incident), incident["incident_id"]))

    def _incidents(self, db):
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM incidents")]

    def _ongoing(self, db, key):
        for row in db.execute("SELECT body FROM incidents WHERE condition_key=?", (key,)):
            item = json.loads(row[0])
            if item["condition_state"] != "restored":
                return item
        return None

    @staticmethod
    def _working(item):
        return item["status"] != "closed" and item.get("disposition") != "rejected_model_signal"

    def _history(self, db, item, action, actor=None, reason=None, request_id=None,
                 from_operator=None, to_operator=None, **extra):
        history = {"history_id": uid("history"), "incident_id": item["incident_id"],
                   "action": action, "actor_operator_id": actor, "from_operator_id": from_operator,
                   "to_operator_id": to_operator, "reason": reason, "created_at": stamp(self.clock()),
                   "dispatch_revision": item["dispatch_revision"], "request_id": request_id, **extra}
        db.execute("INSERT INTO dispatch_history VALUES (?,?,?)",
                   (history["history_id"], item["incident_id"], canonical(history)))
        return history

    def _notify(self, db, item, recipient, kind, cause, instance="initial"):
        notification = {"notification_id": uid("notification"), "incident_id": item["incident_id"],
                        "recipient_operator_id": recipient, "kind": kind, "cause": cause,
                        "cause_instance_id": instance, "created_at": stamp(self.clock()), "read_at": None}
        key = canonical([item["incident_id"], cause, recipient, instance])
        cursor = db.execute("INSERT OR IGNORE INTO notifications(dedupe_key,incident_id,recipient_operator_id,body) VALUES (?,?,?,?)",
                            (key, item["incident_id"], recipient, canonical(notification)))
        return cursor.rowcount == 1

    def _area(self, x, y):
        for area in self.site["site_areas"]:
            if area.get("rectangle") and self._inside(x, y, area["rectangle"]):
                return area["id"]
        for road in self.site["roads"]:
            for a, b in zip(road["points"], road["points"][1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                den = dx * dx + dy * dy
                t = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / den)) if den else 0
                if math.hypot(x - a[0] - t * dx, y - a[1] - t * dy) <= road["width"] / 2:
                    return "common-roads"
        return "unknown"

    @staticmethod
    def _inside(x, y, rectangle):
        return rectangle_contains(x, y, rectangle)

    def _profile_sector(self, operator):
        return self.profiles[operator]["sector_id"]

    def _sector_operator(self, sector):
        return next(p for p in self.profiles if self._profile_sector(p) == sector)

    def _reserves(self, owner):
        return {"dispatcher-1": ["dispatcher-3", "dispatcher-2"],
                "dispatcher-2": ["dispatcher-3", "dispatcher-1"],
                "dispatcher-3": ["dispatcher-1", "dispatcher-2"]}[owner]

    def _escalate(self, db, item, kind, cause, instance="initial"):
        primary = item["assigned_operator_id"] or self._sector_operator(item["responsible_sector_id"])
        recipients = ["dispatcher-3"] if primary != "dispatcher-3" else ["dispatcher-1", "dispatcher-2", "dispatcher-3"]
        changed = False
        for recipient in recipients:
            changed |= self._notify(db, item, recipient, kind, cause, instance)
        if changed:
            item["escalation_level"] += 1
            item["dispatch_revision"] += 1
            self._history(db, item, "escalation", reason=cause)
            self._save_incident(db, item)

    def _new_incident(self, db, kind, key, area_id, evidence, active=True, **fields):
        area = self._require(area_id, self.areas, "site_area")
        contact = {"forbidden_zone": "Диспетчер участка и служба безопасности",
                   "unauthorized_access": "Служба безопасности",
                   "sensor_offline": "Ответственный за датчики",
                   "model_anomaly": "Диспетчер участка"}[kind]
        item = {"incident_id": uid("incident"), "type": kind,
                "severity": "critical" if kind in {"forbidden_zone", "unauthorized_access"} else "warning",
                "detected_at": stamp(self.clock()), "status": "open", "condition_active": active,
                "condition_state": "active" if active else "restored", "rule_version": self.rule_version,
                "evidence_event_ids": list(evidence), "details": {}, "demo": True,
                "site_area_id": area_id, "responsible_sector_id": area["responsible_sector_id"],
                "assigned_operator_id": None, "acknowledged_at": None, "dispatch_revision": 0,
                "pending_transfer": None, "escalation_level": 0, "response_history": [],
                "response_plan": {"contact": contact,
                                  "steps": "Принять случай, связаться с ответственной ролью, проверить свежие наблюдения, записать реакцию."},
                **fields}
        db.execute("INSERT INTO incidents VALUES (?,?,?)", (item["incident_id"], key, canonical(item)))
        self._history(db, item, "detected")
        self._notify(db, item, self._sector_operator(item["responsible_sector_id"]), "new_incident", "new_incident")
        return item

    def _evidence(self, item, event_id):
        if event_id and event_id not in item["evidence_event_ids"]:
            item["evidence_event_ids"].append(event_id)
            # Incidents retain the first trigger and latest 99 observations. Raw history stays append-only.
            if len(item["evidence_event_ids"]) > 100:
                item["evidence_event_ids"] = item["evidence_event_ids"][:1] + item["evidence_event_ids"][-99:]

    def _restore(self, db, item, event_id=None):
        item["condition_active"] = False
        item["condition_state"] = "restored"
        item["restored_at"] = stamp(self.clock())
        self._evidence(item, event_id)
        self._history(db, item, "condition_restored")
        self._save_incident(db, item)

    def _normalize_event(self, event):
        required = {"event_id", "event_time", "sensor_id", "type", "demo", "payload"}
        if not isinstance(event, dict) or set(event) != required:
            raise ApiError(422, "invalid_event", "Event requires exact fields", {"required": sorted(required)})
        for key in ("event_id", "sensor_id"):
            if not isinstance(event[key], str) or not 1 <= len(event[key]) <= 128:
                raise ApiError(422, "invalid_event", key + " must be 1..128 characters")
        if event["demo"] is not True:
            raise ApiError(422, "demo_required", "This simulator accepts demo=true only")
        source_time = parse_time(event["event_time"])
        if (source_time - self.clock()).total_seconds() > self.config["future_event_tolerance_seconds"]:
            raise ApiError(422, "future_event", "Event timestamp exceeds future tolerance")
        sensor = self._require(event["sensor_id"], self.sensors_by_id, "sensor")
        payload = event["payload"]
        if not isinstance(payload, dict):
            raise ApiError(422, "invalid_payload", "payload must be an object")
        kind = event["type"]
        if kind == "position":
            if set(payload) != {"asset_id", "x", "y"}:
                raise ApiError(422, "invalid_payload", "Position requires asset_id,x,y")
            asset = self._require(payload["asset_id"], self.assets_by_id, "asset")
            if sensor["type"] != "position" or sensor.get("asset_id") != asset["id"]:
                raise ApiError(422, "sensor_asset_mismatch", "Position sensor must match vehicle")
            for key in ("x", "y"):
                value = payload[key]
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise ApiError(422, "invalid_coordinate", "Coordinates must be finite and in 0..100")
            payload = {"asset_id": payload["asset_id"], "x": float(payload["x"]), "y": float(payload["y"])}
        elif kind == "access":
            if not {"employee_id", "building_id", "direction"} <= set(payload) or set(payload) - {"employee_id", "building_id", "direction", "access_kind"}:
                raise ApiError(422, "invalid_payload", "Access requires employee_id,building_id,direction")
            asset = self._require(payload["employee_id"], self.assets_by_id, "asset")
            self._require(payload["building_id"], self.buildings, "building")
            if asset["type"] != "employee" or sensor["type"] != "access" or sensor.get("building_id") != payload["building_id"]:
                raise ApiError(422, "sensor_building_mismatch", "Access sensor must match building and employee")
            if payload["direction"] not in ("in", "out") or payload.get("access_kind", "passage_confirmed") != "passage_confirmed":
                raise ApiError(422, "invalid_access", "Only confirmed in/out passage is supported")
            payload = {**payload, "access_kind": "passage_confirmed"}
        elif kind == "heartbeat":
            if payload:
                raise ApiError(422, "invalid_payload", "Heartbeat payload must be empty")
        else:
            raise ApiError(422, "unknown_event_type", "Unsupported event type")
        value = {**event, "event_time": stamp(source_time), "payload": payload}
        if len(canonical(value).encode("utf-8")) > self.config["max_event_bytes"]:
            raise ApiError(422, "event_too_large", "Event exceeds configured size")
        return value

    def ingest_event(self, event, *, shift_id=None):
        value = self._normalize_event(event)
        normalized = canonical(value)
        now = self.clock()
        with self.store.transaction() as db:
            old = db.execute("SELECT normalized,received_at FROM events WHERE event_id=?", (value["event_id"],)).fetchone()
            if old:
                if old["normalized"] != normalized:
                    raise ApiError(409, "event_conflict", "event_id is already used with another body")
                if shift_id:
                    from src.core.operations import correlate_event
                    correlate_event(db, shift_id, value)
                return {"event_id": value["event_id"], "received_at": old["received_at"], "duplicate": True}
            value["received_at"] = stamp(now)
            payload = value["payload"]
            asset_id = payload.get("asset_id", payload.get("employee_id"))
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (value["event_id"], normalized,
                       value["event_time"], value["received_at"], value["sensor_id"], value["type"], asset_id, canonical(value)))
            health = self._load(db, "sensor_state", "sensor_id", value["sensor_id"])
            health["last_received_at"] = value["received_at"]
            health["last_event_id"] = value["event_id"]
            fresh = (now - parse_time(value["event_time"])).total_seconds() < self.config["fresh_position_max_age_seconds"]
            if value["type"] == "heartbeat" and fresh:
                health["last_heartbeat_at"] = value["received_at"]
            if value["type"] != "heartbeat" and (not health["last_measurement_at"] or value["event_time"] > health["last_measurement_at"]):
                health["last_measurement_at"] = value["event_time"]
            if fresh:
                health["status"] = "online"
                offline = self._ongoing(db, "offline:" + value["sensor_id"])
                if offline:
                    self._restore(db, offline, value["event_id"])
            db.execute("UPDATE sensor_state SET body=? WHERE sensor_id=?", (canonical(health), value["sensor_id"]))
            if value["type"] == "position":
                self._position(db, value, fresh)
            elif value["type"] == "access":
                policy = self.asset_policy(payload["employee_id"])
                if payload["direction"] == "in" and payload["building_id"] not in policy["allowed_building_ids"]:
                    building = self.buildings[payload["building_id"]]
                    self._new_incident(db, "unauthorized_access", "access:" + value["event_id"],
                                       building["site_area_id"], [value["event_id"]], active=False,
                                       employee_id=payload["employee_id"], asset_id=payload["employee_id"],
                                       building_id=payload["building_id"], sensor_id=value["sensor_id"],
                                       details={"access_kind": "passage_confirmed", "policy_version": self.site["policy_version"]})
            if shift_id:
                from src.core.operations import correlate_event
                correlate_event(db, shift_id, value)
            return {"event_id": value["event_id"], "received_at": value["received_at"], "duplicate": False}

    def _position(self, db, event, fresh):
        payload = event["payload"]
        asset_id = payload["asset_id"]
        previous = self._load(db, "asset_state", "asset_id", asset_id)
        if previous and (event["event_time"], event["event_id"]) <= (previous["last_seen"], previous["event_id"]):
            return
        state = {**self.assets_by_id[asset_id], "asset_id": asset_id, "x": payload["x"], "y": payload["y"],
                 "last_seen": event["event_time"], "last_received_at": event["received_at"],
                 "event_id": event["event_id"], "site_area_id": self._area(payload["x"], payload["y"]),
                 "position_state": "fresh" if fresh else "unknown"}
        db.execute("INSERT INTO asset_state VALUES (?,?) ON CONFLICT(asset_id) DO UPDATE SET body=excluded.body",
                   (asset_id, canonical(state)))
        if not fresh:
            return
        allowed = self.asset_policy(asset_id)["allowed_zone_ids"]
        gap = previous and (
            previous.get("position_state") == "unknown"
            or (parse_time(event["event_time"]) - parse_time(previous["last_seen"])).total_seconds() >= self.config["position_stale_seconds"]
            or (self.clock() - parse_time(previous["last_seen"])).total_seconds() >= self.config["position_stale_seconds"]
        )
        for zone in self.site["zones"]:
            if zone["id"] in allowed:
                continue
            key = "zone:" + asset_id + ":" + zone["id"]
            incident = self._ongoing(db, key)
            inside = self._inside(payload["x"], payload["y"], zone["rectangle"])
            if inside:
                if not incident:
                    incident = self._new_incident(db, "forbidden_zone", key, zone["site_area_id"], [event["event_id"]],
                                                  asset_id=asset_id, sensor_id=event["sensor_id"], zone_id=zone["id"],
                                                  details={"exit_samples": 0, "policy_version": self.site["policy_version"]})
                incident["condition_state"] = "active"
                incident["condition_active"] = True
                incident["details"]["exit_samples"] = 0
                self._evidence(incident, event["event_id"])
                self._save_incident(db, incident)
            elif incident:
                if gap or incident["condition_state"] == "unknown":
                    incident["details"]["exit_samples"] = 0
                incident["condition_state"] = "active"
                incident["details"]["exit_samples"] = incident["details"].get("exit_samples", 0) + 1
                self._evidence(incident, event["event_id"])
                if incident["details"]["exit_samples"] >= self.config["zone_exit_confirm_samples"]:
                    self._restore(db, incident, event["event_id"])
                else:
                    self._save_incident(db, incident)

    def list_assets(self):
        now = self.clock()
        with self.store.read() as db:
            result = []
            for asset in self.site["assets"]:
                state = self._load(db, "asset_state", "asset_id", asset["id"]) or {**asset, "asset_id": asset["id"], "x": None, "y": None, "last_seen": None, "site_area_id": "unknown"}
                age = max(0, (now - parse_time(state["last_seen"])).total_seconds()) if state["last_seen"] else None
                state["age_seconds"] = age
                state["position_state"] = "fresh" if age is not None and age < self.config["position_stale_seconds"] else "unknown"
                state["stale"] = state["position_state"] == "unknown"
                result.append(state)
            return result

    def asset_policy(self, asset_id):
        self._require(asset_id, self.assets_by_id, "asset")
        item = next((p for p in self.site["permissions"] if p.get("asset_id", p.get("employee_id")) == asset_id), {})
        return {"asset_id": asset_id, "allowed_zone_ids": item.get("allowed_zone_ids", []),
                "allowed_building_ids": item.get("allowed_building_ids", []), "policy_version": self.site["policy_version"]}

    def _sensor_health(self, db, sensor_id):
        sensor = self._require(sensor_id, self.sensors_by_id, "sensor")
        state = self._load(db, "sensor_state", "sensor_id", sensor_id)
        now = self.clock()
        age = (now - parse_time(state["last_received_at"])).total_seconds() if state["last_received_at"] else None
        if age is None:
            status = "unknown" if (now - parse_time(state["first_expected_at"])).total_seconds() < self.config["startup_sensor_grace_seconds"] else "offline"
        else:
            status = "online" if age < self.config["sensor_offline_seconds"] else "offline"
        return {**sensor, **state, "status": status, "age_seconds": age, "as_of": stamp(now),
                "threshold_seconds": self.config["sensor_offline_seconds"],
                "startup_sensor_grace_seconds": self.config["startup_sensor_grace_seconds"]}

    def sensor_health(self, sensor_id):
        with self.store.read() as db:
            return self._sensor_health(db, sensor_id)

    def list_sensors(self, site_area_id=None):
        if site_area_id:
            self._require(site_area_id, self.areas, "site_area")
        with self.store.read() as db:
            result = [self._sensor_health(db, sid) for sid in self.sensors_by_id]
            return [s for s in result if not site_area_id or s.get("site_area_id") == site_area_id]

    def event_history(self, asset_id=None, since=None, until=None, limit=100):
        if asset_id:
            self._require(asset_id, self.assets_by_id, "asset")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ApiError(422, "invalid_limit", "limit must be 1..500")
        since_time = stamp(parse_time(since)) if since else None
        until_time = stamp(parse_time(until)) if until else None
        if since_time and until_time and since_time > until_time:
            raise ApiError(422, "invalid_range", "since must not exceed until")
        clauses, args = [], []
        for column, operation, value in (("asset_id", "=", asset_id), ("event_time", ">=", since_time), ("event_time", "<=", until_time)):
            if value:
                clauses.append(column + operation + "?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.read() as db:
            rows = db.execute("SELECT body FROM events" + where + " ORDER BY event_time,event_id LIMIT ?", (*args, limit))
            return [json.loads(r[0]) for r in rows]

    def _incident_view(self, db, item):
        view = dict(item)
        view["history"] = [json.loads(r[0]) for r in db.execute("SELECT body FROM dispatch_history WHERE incident_id=? ORDER BY rowid", (item["incident_id"],))]
        ids = item["evidence_event_ids"]
        view["evidence"] = [json.loads(r[0]) for r in db.execute("SELECT body FROM events WHERE event_id IN (" + ",".join("?" for _ in ids) + ") ORDER BY event_time,event_id", ids)] if ids else []
        view["model_version"] = item.get("details", {}).get("model_version", self.model_version)
        view["policy_version"] = self.site["policy_version"]
        view["as_of"] = stamp(self.clock())
        view["alarm_state"] = alarm_state(item)
        return view

    def can_claim(self, db, item, operator):
        if not operator or not self._working(item) or item["assigned_operator_id"]:
            return False
        if item["responsible_sector_id"] == self._profile_sector(operator):
            return True
        return bool(db.execute("""SELECT 1 FROM notifications WHERE incident_id=? AND recipient_operator_id=?
            AND json_extract(body,'$.kind') IN ('escalation','operator_unavailable','active_review','transfer_expired') LIMIT 1""",
                               (item["incident_id"], operator)).fetchone())

    def get_incident(self, incident_id, operator=None):
        with self.store.read() as db:
            item = self._load(db, "incidents", "incident_id", incident_id)
            if not item:
                raise ApiError(404, "unknown_incident", "Unknown incident", {"id": incident_id})
            view = self._incident_view(db, item)
            if operator:
                self.validate_operator(operator)
                view["can_claim"] = self.can_claim(db, item, operator)
            return view

    def list_incidents(self, status=None, scope=None, site_area_id=None, operator=None):
        if status and status not in {"open", "acknowledged", "closed"}:
            raise ApiError(422, "invalid_status", "Unsupported status")
        if scope and scope not in {"all", "workstation"}:
            raise ApiError(422, "invalid_scope", "scope must be all or workstation")
        if site_area_id:
            self._require(site_area_id, self.areas, "site_area")
        if operator or scope == "workstation":
            self.validate_operator(operator)
        with self.store.read() as db:
            addressed = set()
            if scope == "workstation":
                addressed = {r[0] for r in db.execute("SELECT incident_id FROM notifications WHERE recipient_operator_id=?", (operator,))}
            result = []
            for item in self._incidents(db):
                if status and item["status"] != status or site_area_id and item["site_area_id"] != site_area_id:
                    continue
                transfer = item.get("pending_transfer") or {}
                if scope == "workstation" and not (item["responsible_sector_id"] == self._profile_sector(operator) or item["assigned_operator_id"] == operator or transfer.get("to_operator_id") == operator or item["incident_id"] in addressed):
                    continue
                result.append({**item, "alarm_state": alarm_state(item),
                               **({"can_claim": self.can_claim(db, item, operator)} if operator else {})})
            priority = {"critical": 0, "warning": 1, "info": 2}
            return sorted(result, key=lambda i: (priority[i["severity"]], bool(i["assigned_operator_id"]), i["detected_at"], i["incident_id"]))

    def _presence(self, db, operator):
        now = self.clock()
        sessions = [json.loads(r[0]) for r in db.execute("SELECT body FROM presence WHERE operator_id=?", (operator,))]
        fresh = [s for s in sessions if (now - parse_time(s["last_seen_at"])).total_seconds() < self.config["presence_timeout_seconds"]]
        last = max((s["last_seen_at"] for s in sessions), default=None)
        return {"operator_id": operator, "client_online": bool(fresh), "operator_online": bool(fresh),
                "operator_ready": any(s["availability"] == "ready" for s in fresh), "last_seen_at": last}

    def operator_presence(self, operator, body):
        self.validate_operator(operator)
        if not isinstance(body, dict) or set(body) - {"session_id", "availability"} or not isinstance(body.get("session_id"), str) or not 1 <= len(body["session_id"]) <= 128 or not isinstance(body.get("availability", "ready"), str) or body.get("availability", "ready") not in {"ready", "away"}:
            raise ApiError(422, "invalid_presence", "Expected session_id and availability ready/away")
        with self.store.transaction() as db:
            # Preserve expired ready leases before a heartbeat overwrites their timestamp.
            self._sync_absence(db, operator)
            value = {"session_id": body["session_id"], "availability": body.get("availability", "ready"), "last_seen_at": stamp(self.clock())}
            db.execute("INSERT INTO presence VALUES (?,?,?) ON CONFLICT(operator_id,session_id) DO UPDATE SET body=excluded.body", (operator, value["session_id"], canonical(value)))
            result = self._presence(db, operator)
            self._sync_absence(db, operator)
            return {**value, **result, "online": result["client_online"]}

    def operator_profiles(self):
        with self.store.read() as db:
            return [{"operator_id": operator, "name": p["name"], "sector_id": p["sector_id"], **self._presence(db, operator)} for operator, p in self.profiles.items()]

    def notifications(self, operator, after_seq=0, limit=50):
        self.validate_operator(operator)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ApiError(422, "invalid_cursor", "after_seq>=0 and limit 1..100 required")
        with self.store.read() as db:
            rows = db.execute("SELECT seq,body FROM notifications WHERE recipient_operator_id=? AND seq>? ORDER BY seq LIMIT ?", (operator, after_seq, limit)).fetchall()
            values = [{**json.loads(r["body"]), "seq": r["seq"]} for r in rows]
            return {"notifications": values, "next_seq": values[-1]["seq"] if values else after_seq}

    def summary(self, by_area=False):
        with self.store.read() as db:
            incidents = self._incidents(db)
            result = []
            for group in self.site["site_areas"] if by_area else self.site["sectors"]:
                key = "site_area_id" if by_area else "responsible_sector_id"
                items = [i for i in incidents if i[key] == group["id"] and i.get("disposition") != "rejected_model_signal"]
                entry = {"site_area_id" if by_area else "sector_id": group["id"],
                         "active_count": sum(bool(i["condition_active"]) for i in items),
                         "unclaimed_count": sum(self._working(i) and not i["assigned_operator_id"] for i in items),
                         "escalated_count": sum(self._working(i) and i["escalation_level"] > 0 for i in items)}
                if not by_area:
                    presence = self._presence(db, self._sector_operator(group["id"]))
                    entry.update({"operator_online": presence["client_online"], "operator_ready": presence["operator_ready"]})
                result.append(entry)
            return {"as_of": stamp(self.clock()), "site_areas" if by_area else "sectors": result,
                    "unknown_count": sum(self._working(i) and i["site_area_id"] == "unknown" for i in incidents)}

    def _absence(self, db, operator):
        row = db.execute("SELECT value FROM metadata WHERE key=?", ("absence:" + operator,)).fetchone()
        return json.loads(row[0]) if row else None

    def _sync_absence(self, db, operator):
        presence = self._presence(db, operator)
        absence = self._absence(db, operator)
        if presence["operator_ready"]:
            if absence:
                db.execute("UPDATE metadata SET value='null' WHERE key=?", ("absence:" + operator,))
            return None
        if absence is None:
            # An expired ready lease establishes the start of unavailability at its expiry.
            since = self.clock()
            ready_leases = [json.loads(row[0]) for row in db.execute("SELECT body FROM presence WHERE operator_id=?", (operator,))]
            ready_deadlines = [parse_time(session["last_seen_at"]) + timedelta(seconds=self.config["presence_timeout_seconds"])
                               for session in ready_leases if session["availability"] == "ready"]
            if ready_deadlines:
                since = min(since, max(ready_deadlines))
            absence = {"since": stamp(since), "episode_id": uid("absence")}
            db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       ("absence:" + operator, canonical(absence)))
        return absence

    def _validate_action(self, body):
        if not isinstance(body, dict) or "action" not in body:
            raise ApiError(422, "action_required", "Use typed v2 action, expected_revision and request_id")
        extra = {"claim": set(), "close": {"reason"}, "record_response": {"reason", "response_code"},
                 "dismiss_model": {"reason"}, "request_transfer": {"to_operator_id", "reason"},
                 "accept_transfer": {"transfer_id"}, "cancel_transfer": {"transfer_id", "reason"},
                 "reassign_unavailable": {"to_operator_id", "reason"}}
        action = body["action"]
        if not isinstance(action, str) or action not in extra:
            raise ApiError(422, "invalid_action", "Unknown coordination action")
        required = {"action", "expected_revision", "request_id"} | extra[action]
        if set(body) != required:
            raise ApiError(422, "invalid_action_fields", "Unexpected or missing action fields", {"required": sorted(required)})
        revision = body["expected_revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ApiError(422, "invalid_revision", "expected_revision must be a nonnegative integer")
        if not isinstance(body["request_id"], str) or not 1 <= len(body["request_id"]) <= 128:
            raise ApiError(422, "invalid_request_id", "request_id must be 1..128 characters")
        if "reason" in required and (not isinstance(body["reason"], str) or not 1 <= len(body["reason"]) <= 500 or not body["reason"].strip()):
            raise ApiError(422, "invalid_reason", "reason must be 1..500 nonblank characters")
        if action == "record_response" and (not isinstance(body["response_code"], str) or body["response_code"] not in {"contacted", "inspection_requested", "checked"}):
            raise ApiError(422, "invalid_response_code", "Unsupported response code")
        for key in ("transfer_id", "to_operator_id"):
            if key in body and (not isinstance(body[key], str) or not body[key]):
                raise ApiError(422, "invalid_identifier", key + " must be a nonempty string")
        return body

    def action(self, incident_id, operator, body):
        self.validate_operator(operator)
        body = self._validate_action(body)
        normalized = canonical(body)
        with self.store.transaction() as db:
            previous = db.execute("SELECT normalized,response FROM requests WHERE incident_id=? AND operator_id=? AND request_id=?",
                                  (incident_id, operator, body["request_id"])).fetchone()
            if previous:
                if previous["normalized"] != normalized:
                    raise ApiError(409, "request_conflict", "request_id has already been used with another body")
                return json.loads(previous["response"])
            item = self._load(db, "incidents", "incident_id", incident_id)
            if not item:
                raise ApiError(404, "unknown_incident", "Unknown incident")
            if body["expected_revision"] != item["dispatch_revision"]:
                raise ApiError(409, "revision_conflict", "Incident revision has changed",
                               {"dispatch_revision": item["dispatch_revision"], "incident": self._incident_view(db, item)})
            if not self._working(item):
                raise ApiError(409, "incident_closed", "Incident is no longer a working task")
            action = body["action"]
            original_owner = item["assigned_operator_id"]
            to_operator = body.get("to_operator_id")
            if to_operator:
                self._require(to_operator, self.profiles, "operator")
                if to_operator == original_owner:
                    raise ApiError(422, "self_transfer", "Recipient must be a different operator")
            if action == "claim":
                if original_owner:
                    raise ApiError(409, "already_claimed", "Incident already has an owner")
                if not self.can_claim(db, item, operator):
                    raise ApiError(409, "operator_conflict", "Only responsible operator or addressed reserve may claim")
                item["assigned_operator_id"] = operator
                item["status"] = "acknowledged"
                item["acknowledged_at"] = stamp(self.clock())
            elif action == "accept_transfer":
                transfer = self._load(db, "transfers", "transfer_id", body["transfer_id"])
                if not transfer or transfer["incident_id"] != incident_id:
                    raise ApiError(404, "unknown_transfer", "Unknown transfer")
                if transfer["status"] != "pending" or transfer["to_operator_id"] != operator or parse_time(transfer["expires_at"]) <= self.clock():
                    raise ApiError(409, "transfer_conflict", "Transfer is not pending for this recipient")
                if not item["pending_transfer"] or item["pending_transfer"]["transfer_id"] != transfer["transfer_id"]:
                    raise ApiError(409, "transfer_conflict", "Transfer is no longer current")
                transfer["status"] = "accepted"
                transfer["accepted_at"] = stamp(self.clock())
                item["assigned_operator_id"] = operator
                item["pending_transfer"] = None
                db.execute("UPDATE transfers SET body=? WHERE transfer_id=?", (canonical(transfer), transfer["transfer_id"]))
                self._notify(db, item, original_owner, "transfer_accepted", "transfer_accepted", transfer["transfer_id"])
            elif action == "reassign_unavailable":
                if not original_owner:
                    raise ApiError(409, "operator_conflict", "Recovery reassignment needs an existing owner")
                if (self.clock() - self.started_at).total_seconds() < self.config["startup_operator_grace_seconds"]:
                    raise ApiError(409, "startup_grace", "Browser leases are being restored after startup")
                absence = self._sync_absence(db, original_owner)
                if not absence or (self.clock() - parse_time(absence["since"])).total_seconds() < self.config["presence_timeout_seconds"]:
                    raise ApiError(409, "operator_available", "Owner unavailability duration is not confirmed")
                first = next((p for p in self._reserves(original_owner) if self._presence(db, p)["operator_ready"]), None)
                if operator != first or not self._presence(db, to_operator)["operator_ready"]:
                    raise ApiError(409, "operator_conflict", "Only first ready reserve may reassign to a ready recipient")
                if item["pending_transfer"]:
                    transfer = item["pending_transfer"]
                    transfer["status"] = "cancelled"
                    transfer["cancel_reason"] = "recovery"
                    db.execute("UPDATE transfers SET body=? WHERE transfer_id=?", (canonical(transfer), transfer["transfer_id"]))
                    item["pending_transfer"] = None
                item["assigned_operator_id"] = to_operator
                item["status"] = "acknowledged"
                item["acknowledged_at"] = stamp(self.clock())
            else:
                if original_owner != operator:
                    raise ApiError(409, "operator_conflict", "Only current owner may perform this action")
                if action == "close":
                    if item["condition_state"] == "unknown":
                        raise ApiError(409, "condition_unknown", "Fresh detector observations are required")
                    if item["condition_active"]:
                        raise ApiError(409, "condition_active", "Active condition cannot be closed")
                    if item["pending_transfer"]:
                        raise ApiError(409, "transfer_pending", "Cancel pending transfer before closing")
                    item["status"] = "closed"
                    item["closed_at"] = stamp(self.clock())
                elif action == "record_response":
                    item["response_history"].append({"response_code": body["response_code"], "reason": body["reason"],
                                                     "actor_operator_id": operator, "created_at": stamp(self.clock())})
                elif action == "dismiss_model":
                    if item["type"] != "model_anomaly":
                        raise ApiError(409, "wrong_incident_type", "Only model suspicion may be dismissed")
                    item["disposition"] = "rejected_model_signal"
                    item["disposition_reason"] = body["reason"]
                    item["reviewed_at"] = stamp(self.clock())
                elif action == "request_transfer":
                    if item["pending_transfer"]:
                        raise ApiError(409, "transfer_pending", "Only one pending transfer is allowed")
                    if not self._presence(db, to_operator)["operator_ready"]:
                        raise ApiError(409, "operator_unavailable", "Recipient must be online and ready")
                    transfer = {"transfer_id": uid("transfer"), "incident_id": incident_id,
                                "from_operator_id": operator, "to_operator_id": to_operator,
                                "requested_at": stamp(self.clock()), "expires_at": stamp(self.clock() + timedelta(seconds=self.config["transfer_timeout_seconds"])),
                                "status": "pending", "reason": body["reason"]}
                    item["pending_transfer"] = transfer
                    db.execute("INSERT INTO transfers VALUES (?,?,?)", (transfer["transfer_id"], incident_id, canonical(transfer)))
                    self._notify(db, item, to_operator, "transfer_requested", "transfer_requested", transfer["transfer_id"])
                elif action == "cancel_transfer":
                    transfer = self._load(db, "transfers", "transfer_id", body["transfer_id"])
                    if not transfer or transfer["incident_id"] != incident_id:
                        raise ApiError(404, "unknown_transfer", "Unknown transfer")
                    if transfer["status"] != "pending" or not item["pending_transfer"] or transfer["transfer_id"] != item["pending_transfer"]["transfer_id"]:
                        raise ApiError(409, "transfer_conflict", "Transfer is not current")
                    transfer["status"] = "cancelled"
                    transfer["cancel_reason"] = body["reason"]
                    db.execute("UPDATE transfers SET body=? WHERE transfer_id=?", (canonical(transfer), transfer["transfer_id"]))
                    item["pending_transfer"] = None
            item["dispatch_revision"] += 1
            self._history(db, item, action, actor=operator, reason=body.get("reason"), request_id=body["request_id"],
                          from_operator=original_owner, to_operator=item["assigned_operator_id"] if action in {"claim", "accept_transfer", "reassign_unavailable"} else to_operator,
                          **({"response_code": body["response_code"]} if action == "record_response" else {}))
            self._save_incident(db, item)
            response = self._incident_view(db, item)
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (incident_id, operator, body["request_id"], normalized, canonical(response)))
            return response

    def tick(self):
        """One global 1 Hz timer. All deadlines and de-duplication survive restart."""
        now = self.clock()
        with self.store.transaction() as db:
            for sid, sensor in self.sensors_by_id.items():
                health = self._sensor_health(db, sid)
                incident = self._ongoing(db, "offline:" + sid)
                if health["status"] == "offline" and not incident:
                    area = sensor.get("site_area_id")
                    asset_id = sensor.get("asset_id")
                    asset = self._load(db, "asset_state", "asset_id", asset_id) if asset_id else None
                    if not area:
                        fresh = asset and (now - parse_time(asset["last_seen"])).total_seconds() < self.config["position_stale_seconds"]
                        area = asset["site_area_id"] if fresh else "unknown"
                    self._new_incident(db, "sensor_offline", "offline:" + sid, area,
                                       [health["last_event_id"]] if health["last_event_id"] else [],
                                       asset_id=asset_id, sensor_id=sid,
                                       details={"cause": "never_started" if not health["last_received_at"] else "connection_lost",
                                                "last_received_at": health["last_received_at"], "first_expected_at": health["first_expected_at"],
                                                "threshold_seconds": health["threshold_seconds"], "sensor_health": health})
            for item in self._incidents(db):
                if item["type"] in {"forbidden_zone", "model_anomaly"} and item["condition_state"] != "restored":
                    state = self._load(db, "asset_state", "asset_id", item["asset_id"])
                    if not state or (now - parse_time(state["last_seen"])).total_seconds() >= self.config["position_stale_seconds"]:
                        item["condition_state"] = "unknown"
                        item["details"]["exit_samples"] = 0
                        self._save_incident(db, item)
            grace_done = (now - self.started_at).total_seconds() >= self.config["startup_operator_grace_seconds"]
            absences = {operator: self._sync_absence(db, operator) for operator in self.profiles}
            for item in self._incidents(db):
                if not self._working(item):
                    continue
                transfer = item["pending_transfer"]
                if transfer and parse_time(transfer["expires_at"]) <= now:
                    transfer["status"] = "expired"
                    transfer["expired_at"] = stamp(now)
                    db.execute("UPDATE transfers SET body=? WHERE transfer_id=?", (canonical(transfer), transfer["transfer_id"]))
                    item["pending_transfer"] = None
                    item["dispatch_revision"] += 1
                    self._history(db, item, "transfer_expired", from_operator=transfer["from_operator_id"], to_operator=transfer["to_operator_id"])
                    self._notify(db, item, transfer["from_operator_id"], "transfer_expired", "transfer_expired", transfer["transfer_id"])
                    self._escalate(db, item, "transfer_expired", "transfer_expired_coordination", transfer["transfer_id"])
                    self._save_incident(db, item)
                owner = item["assigned_operator_id"]
                if not owner:
                    age = (now - parse_time(item["detected_at"])).total_seconds()
                    primary = self._sector_operator(item["responsible_sector_id"])
                    if age >= self.config["reminder_seconds"]:
                        self._notify(db, item, primary, "reminder", "unclaimed_reminder")
                    if age >= self.config["escalation_" + item["severity"] + "_seconds"]:
                        self._escalate(db, item, "escalation", "unclaimed_escalation")
                    if grace_done and absences[primary]:
                        self._escalate(db, item, "operator_unavailable", "primary_unavailable", absences[primary]["episode_id"])
                else:
                    if grace_done and absences[owner]:
                        self._escalate(db, item, "operator_unavailable", "owner_unavailable", absences[owner]["episode_id"])
                    if item["condition_active"] and item["acknowledged_at"] and (now - parse_time(item["acknowledged_at"])).total_seconds() >= self.config["active_review_seconds"]:
                        self._notify(db, item, owner, "active_review", "active_review_owner", item["acknowledged_at"])
                        self._escalate(db, item, "active_review", "active_review_coordination", item["acknowledged_at"])

    def register_model_observation(self, observation):
        self._require(observation.get("asset_id"), self.assets_by_id, "asset")
        for field in ("observation_id", "window_start", "window_end", "status", "model_version", "feature_version", "evidence_event_ids"):
            if field not in observation:
                raise ApiError(422, "invalid_observation", "Missing observation field", {"field": field})
        parse_time(observation["window_start"])
        parse_time(observation["window_end"])
        item = {**observation, "demo": True}
        key = "model:" + item["asset_id"]
        with self.store.transaction() as db:
            previous = self._load(db, "model_observations", "observation_id", item["observation_id"])
            if previous:
                if canonical(previous) != canonical(item):
                    raise ApiError(409, "observation_conflict", "Observation id has another body")
                return previous
            for event_id in item["evidence_event_ids"]:
                if not db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                    raise ApiError(422, "invalid_evidence", "Model evidence must be stored events", {"event_id": event_id})
            db.execute("INSERT INTO model_observations VALUES (?,?,?,?)", (item["observation_id"], item["asset_id"], item["window_end"], canonical(item)))
            self.model_version = item["model_version"]
            incident = self._ongoing(db, key)
            status = item["status"]
            has_score = item.get("score") is not None and item.get("threshold") is not None
            anomaly = status in {"anomaly", "model_anomaly"} or (status in {"ok", "scored", "complete", "normal"} and has_score and item["score"] >= item["threshold"])
            normal = status == "normal" or status in {"ok", "scored", "complete"} and has_score and item["score"] < item["threshold"]
            if anomaly:
                state = self._load(db, "asset_state", "asset_id", item["asset_id"])
                area = state["site_area_id"] if state else "unknown"
                if not incident:
                    incident = self._new_incident(db, "model_anomaly", key, area, item["evidence_event_ids"],
                                                  asset_id=item["asset_id"], sensor_id="POS-" + item["asset_id"],
                                                  details={"normal_windows": 0})
                incident["condition_state"] = "active"
                incident["condition_active"] = True
                incident["details"].update({"normal_windows": 0, "score": item.get("score"), "threshold": item.get("threshold"),
                                             "model_version": item["model_version"], "observation_id": item["observation_id"]})
                for eid in item["evidence_event_ids"]:
                    self._evidence(incident, eid)
                self._save_incident(db, incident)
            elif incident and normal:
                incident["details"]["normal_windows"] = incident["details"].get("normal_windows", 0) + 1
                if incident["details"]["normal_windows"] >= self.config["model_normal_windows"]:
                    self._restore(db, incident)
                else:
                    self._save_incident(db, incident)
            elif incident:
                # Insufficient input is unknown and breaks consecutive normal windows.
                incident["condition_state"] = "unknown"
                incident["details"]["normal_windows"] = 0
                self._save_incident(db, incident)
            return item

    def model_observations(self, asset_id=None, limit=100):
        if asset_id:
            self._require(asset_id, self.assets_by_id, "asset")
        if not 1 <= limit <= 500:
            raise ApiError(422, "invalid_limit", "limit must be 1..500")
        with self.store.read() as db:
            rows = db.execute("SELECT body FROM model_observations" + (" WHERE asset_id=?" if asset_id else "") + " ORDER BY window_end DESC,observation_id LIMIT ?",
                              (asset_id, limit) if asset_id else (limit,))
            return [json.loads(r[0]) for r in rows]
