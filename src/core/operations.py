"""Checkpoint and shift projections over confirmed Events and real dispatch history."""
import csv
import io
import json

from src.core.service import ApiError, canonical, parse_time, stamp, uid


def csv_bytes(columns, rows):
    """UTF-8 BOM for spreadsheet readers; text never becomes a spreadsheet formula."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            value = "" if value is None else str(value)
            if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def correlate_event(db, shift_id, event):
    """Called inside Service's ingestion transaction. Never changes the Event contract."""
    previous = db.execute("SELECT shift_id FROM ops_shift_events WHERE event_id=?", (event["event_id"],)).fetchone()
    if previous:
        if previous["shift_id"] != shift_id:
            raise ApiError(409, "shift_event_conflict", "Event already belongs to another shift")
        return
    row = db.execute("SELECT phase FROM ops_shifts WHERE shift_id=?", (shift_id,)).fetchone()
    if not row or row["phase"] not in ("gate_checks", "transport"):
        raise ApiError(409, "shift_not_active", "Cannot attach an event to an inactive shift")
    kind = None
    if event["type"] == "access" and event["payload"]["building_id"] == "G1":
        kind = "gate"
    elif event["type"] == "position" and row["phase"] == "transport":
        kind = "transport"
    if kind:
        db.execute("INSERT OR IGNORE INTO ops_shift_events VALUES (?,?,?)", (event["event_id"], shift_id, kind))


class Operations:
    def __init__(self, service):
        self.service = service
        with service.store.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ops_shifts (
                    shift_id TEXT PRIMARY KEY, scenario TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, phase TEXT NOT NULL,
                    expected_employee_ids TEXT NOT NULL, transport_started_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS ops_shift_events (
                    event_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ops_shift_event_run ON ops_shift_events(shift_id,kind);
                CREATE INDEX IF NOT EXISTS ops_checkpoint_history ON events(type,event_time,event_id);
            """)
            if not getattr(service, "_operations_initialized", False):
                db.execute("UPDATE ops_shifts SET phase='interrupted',ended_at=? WHERE ended_at IS NULL", (stamp(service.clock()),))
                service._operations_initialized = True

    @staticmethod
    def _page(limit, offset):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ApiError(422, "invalid_limit", "limit must be 1..200")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
            raise ApiError(422, "invalid_offset", "offset must be 0..100000")

    def _checkpoint_sql(self, q, direction, permission, since, until):
        if direction not in (None, "", "in", "out"):
            raise ApiError(422, "invalid_direction", "direction must be in or out")
        if permission not in (None, "", "allowed", "violation"):
            raise ApiError(422, "invalid_permission", "permission must be allowed or violation")
        if q is not None and (not isinstance(q, str) or len(q) > 100):
            raise ApiError(422, "invalid_search", "q must be at most 100 characters")
        since = stamp(parse_time(since)) if since else None
        until = stamp(parse_time(until)) if until else None
        if since and until and since > until:
            raise ApiError(422, "invalid_range", "since must not exceed until")
        employees = [a for a in self.service.site["assets"] if a["type"] == "employee"]
        name_expression = "CASE e.asset_id " + " ".join("WHEN ? THEN ?" for _ in employees) + " ELSE e.asset_id END"
        parameters = [v for asset in employees for v in (asset["id"], asset.get("name", asset["id"]))]
        sql = """WITH checkpoint AS (
            SELECT e.event_id,e.event_time,e.received_at,e.asset_id AS employee_id,
                   %s AS employee_name,json_extract(e.body,'$.payload.building_id') AS building_id,
                   json_extract(e.body,'$.payload.direction') AS direction,
                   CASE WHEN i.incident_id IS NULL THEN 'allowed' ELSE 'violation' END AS permission,
                   m.shift_id,s.scenario
            FROM events e LEFT JOIN incidents i ON i.condition_key='access:'||e.event_id
            LEFT JOIN ops_shift_events m ON m.event_id=e.event_id
            LEFT JOIN ops_shifts s ON s.shift_id=m.shift_id
            WHERE e.type='access' AND json_extract(e.body,'$.payload.building_id')='G1'
        ) SELECT * FROM checkpoint""" % name_expression
        clauses = []
        if q and q.strip():
            query = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(employee_id LIKE ? ESCAPE '\\' OR employee_name LIKE ? ESCAPE '\\' OR event_id LIKE ? ESCAPE '\\')")
            parameters.extend(["%" + query + "%"] * 3)
        for field, operation, value in (("direction", "=", direction), ("permission", "=", permission), ("event_time", ">=", since), ("event_time", "<=", until)):
            if value:
                clauses.append(field + operation + "?")
                parameters.append(value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return sql, parameters

    def _occupancy(self, db):
        rows = list(db.execute("""WITH latest AS (
            SELECT asset_id,event_time,json_extract(body,'$.payload.direction') AS direction,
                row_number() OVER (PARTITION BY asset_id ORDER BY event_time DESC,event_id DESC) AS rn
            FROM events WHERE type='access' AND json_extract(body,'$.payload.building_id')='G1'
        ) SELECT asset_id,event_time,direction FROM latest WHERE rn=1"""))
        sensors = [self.service._sensor_health(db, sid) for sid, sensor in self.service.sensors_by_id.items()
                   if sensor.get("building_id") == "G1" and sensor["type"] == "access"]
        return {"observed_inside_count": sum(row["direction"] == "in" for row in rows),
                "observed_employees_count": len(rows), "initial_state_known": False,
                "certainty": "observed_only", "source": "confirmed_gate_events",
                "last_passage_at": max((row["event_time"] for row in rows), default=None),
                "gate_source_online": bool(sensors) and all(sensor["status"] == "online" for sensor in sensors),
                "note": "Начальное присутствие неизвестно; показано состояние по полученным проходам."}

    def journal(self, q=None, direction=None, permission=None, since=None, until=None, limit=50, offset=0):
        self._page(limit, offset)
        sql, parameters = self._checkpoint_sql(q, direction, permission, since, until)
        with self.service.store.read() as db:
            total = db.execute("SELECT count(*) FROM (" + sql + ")", parameters).fetchone()[0]
            rows = db.execute(sql + " ORDER BY event_time DESC,event_id DESC LIMIT ? OFFSET ?", (*parameters, limit, offset))
            return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset,
                    "as_of": stamp(self.service.clock()), "occupancy": self._occupancy(db)}

    def checkpoint_csv(self, **filters):
        filters.setdefault("limit", 200)
        return csv_bytes(["event_id", "event_time", "received_at", "employee_id", "employee_name", "building_id",
                          "direction", "permission", "shift_id", "scenario"], self.journal(**filters)["items"])

    def start_shift(self, employee_ids):
        ids = list(dict.fromkeys(employee_ids))
        if not ids or len(ids) > 100:
            raise ApiError(422, "invalid_shift", "Shift requires 1..100 unique employees")
        if not any(sensor["type"] == "access" and sensor.get("building_id") == "G1" for sensor in self.service.sensors_by_id.values()):
            raise ApiError(422, "gate_sensor_required", "Shift requires an access sensor at G1")
        for employee_id in ids:
            asset = self.service._require(employee_id, self.service.assets_by_id, "asset")
            if asset["type"] != "employee" or "G1" not in self.service.asset_policy(employee_id)["allowed_building_ids"]:
                raise ApiError(422, "shift_permission_required", "Each expected employee needs G1 admission")
        shift_id = uid("shift")
        now = stamp(self.service.clock())
        with self.service.store.transaction() as db:
            db.execute("UPDATE ops_shifts SET phase='stopped',ended_at=? WHERE ended_at IS NULL", (now,))
            db.execute("INSERT INTO ops_shifts VALUES (?,?,?,NULL,'gate_checks',?,NULL,NULL)", (shift_id, "shift", now, canonical(ids)))
        return shift_id

    def finish_shift(self, shift_id, error=None):
        if not shift_id:
            return
        with self.service.store.transaction() as db:
            db.execute("UPDATE ops_shifts SET phase=?,ended_at=?,error=? WHERE shift_id=? AND ended_at IS NULL",
                       ("error" if error else "stopped", stamp(self.service.clock()), error, shift_id))

    def _shift_view(self, db, row):
        item = dict(row)
        expected = json.loads(item.pop("expected_employee_ids"))
        checked = [record[0] for record in db.execute("""SELECT DISTINCT e.asset_id FROM ops_shift_events m
            JOIN events e ON e.event_id=m.event_id LEFT JOIN incidents i ON i.condition_key='access:'||e.event_id
            WHERE m.shift_id=? AND m.kind='gate' AND json_extract(e.body,'$.payload.direction')='in'
            AND i.incident_id IS NULL""", (item["shift_id"],))]
        counts = dict(db.execute("SELECT kind,count(*) FROM ops_shift_events WHERE shift_id=? GROUP BY kind", (item["shift_id"],)))
        transport = [json.loads(record[0]) for record in db.execute("""SELECT e.body FROM ops_shift_events m
            JOIN events e ON e.event_id=m.event_id WHERE m.shift_id=? AND m.kind='transport'
            ORDER BY e.event_time DESC,e.event_id DESC LIMIT 12""", (item["shift_id"],))]
        item.update(expected_employee_ids=expected, checked_employee_ids=sorted(set(expected) & set(checked)),
                    expected_gate_count=len(expected), gate_count=len(set(expected) & set(checked)),
                    gate_event_count=counts.get("gate", 0), transport_event_count=counts.get("transport", 0),
                    transport_events_count=counts.get("transport", 0),
                    transport_asset_ids=sorted({event["payload"]["asset_id"] for event in transport}),
                    recent_transport_events=[{"event_id": event["event_id"], "event_time": event["event_time"], **event["payload"]} for event in transport],
                    cleared=set(expected) <= set(checked), as_of=stamp(self.service.clock()))
        return item

    def shift(self, shift_id):
        with self.service.store.read() as db:
            row = db.execute("SELECT * FROM ops_shifts WHERE shift_id=?", (shift_id,)).fetchone()
            return self._shift_view(db, row) if row else None

    def current_shift(self):
        with self.service.store.read() as db:
            row = db.execute("SELECT * FROM ops_shifts ORDER BY started_at DESC,rowid DESC LIMIT 1").fetchone()
            return self._shift_view(db, row) if row else None

    def release_transport(self, shift_id):
        with self.service.store.transaction() as db:
            row = db.execute("SELECT * FROM ops_shifts WHERE shift_id=?", (shift_id,)).fetchone()
            if not row or row["phase"] != "gate_checks":
                return bool(row and row["phase"] == "transport")
            if not self._shift_view(db, row)["cleared"]:
                return False
            db.execute("UPDATE ops_shifts SET phase='transport',transport_started_at=? WHERE shift_id=?", (stamp(self.service.clock()), shift_id))
            return True

    def dispatch_activity(self, operator_id):
        self.service.validate_operator(operator_id)
        with self.service.store.read() as db:
            row = db.execute("""SELECT
                coalesce(sum(json_extract(body,'$.action')='claim' AND json_extract(body,'$.actor_operator_id')=?),0) AS claimed,
                coalesce(sum(json_extract(body,'$.action')='accept_transfer' AND json_extract(body,'$.from_operator_id')=?),0) AS transferred,
                coalesce(sum(json_extract(body,'$.action')='close' AND json_extract(body,'$.actor_operator_id')=?),0) AS closed,
                coalesce(sum(json_extract(body,'$.action')='record_response' AND json_extract(body,'$.actor_operator_id')=?),0) AS responses
                FROM dispatch_history""", (operator_id,) * 4).fetchone()
            return {"operator_id": operator_id, **dict(row), "as_of": stamp(self.service.clock())}

    def dispatch_history_csv(self, operator_id=None, limit=1000):
        if operator_id is not None:
            self.service.validate_operator(operator_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise ApiError(422, "invalid_limit", "limit must be 1..2000")
        where = " WHERE json_extract(body,'$.actor_operator_id')=?" if operator_id else ""
        parameters = (operator_id, limit) if operator_id else (limit,)
        with self.service.store.read() as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM dispatch_history" + where + " ORDER BY rowid DESC LIMIT ?", parameters)]
        return csv_bytes(["history_id", "created_at", "incident_id", "action", "actor_operator_id", "from_operator_id", "to_operator_id", "reason", "response_code"], rows)
