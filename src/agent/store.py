"""Only B1 job tables; can share A1's SQLite file without a second domain database."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from src.ml.features import iso
from datetime import datetime, timezone

from .errors import AgentError
from .providers import FrozenSnapshot, canonical


def timestamp(value):
    return iso(datetime.fromtimestamp(value, timezone.utc)) if value is not None else None


class JobStore:
    def __init__(self, path, config, clock=time.time):
        self.path, self.config, self.clock = str(path), config, clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS b1_agent_jobs (
                    job_id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, incident_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, status TEXT NOT NULL,
                    queued_at REAL NOT NULL, queue_deadline REAL NOT NULL,
                    execution_json TEXT NOT NULL DEFAULT '{}', started_at REAL, completed_at REAL,
                    result_json TEXT, error_json TEXT);
                CREATE INDEX IF NOT EXISTS b1_agent_cache ON b1_agent_jobs(cache_key,status);
                CREATE INDEX IF NOT EXISTS b1_agent_fifo ON b1_agent_jobs(status,queued_at);
                CREATE TABLE IF NOT EXISTS b1_agent_requesters (
                    job_id TEXT NOT NULL REFERENCES b1_agent_jobs(job_id),
                    operator_id TEXT NOT NULL, requested_at REAL NOT NULL,
                    PRIMARY KEY(job_id,operator_id));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(b1_agent_jobs)")}
            if "execution_json" not in columns:
                db.execute("ALTER TABLE b1_agent_jobs ADD COLUMN execution_json TEXT NOT NULL DEFAULT '{}'")
            if "queue_deadline" not in columns:
                db.execute("ALTER TABLE b1_agent_jobs ADD COLUMN queue_deadline REAL NOT NULL DEFAULT 0")
                db.execute("UPDATE b1_agent_jobs SET queue_deadline=queued_at+120")

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _expire(self, db, now):
        error = canonical(AgentError("queue_timeout", "Original queue waiting budget expired.").as_dict())
        db.execute("UPDATE b1_agent_jobs SET status='failed',completed_at=?,error_json=? "
                   "WHERE status='queued' AND queue_deadline<=?",
                   (now, error, now))

    def _cached(self, db, cache_key):
        return db.execute("SELECT * FROM b1_agent_jobs WHERE cache_key=? "
                          "AND status IN ('queued','running','completed') ORDER BY queued_at DESC LIMIT 1",
                          (cache_key,)).fetchone()

    def cached(self, cache_key, operator_id):
        with self.connection(write=True) as db:
            self._expire(db, self.clock())
            row = self._cached(db, cache_key)
            if row:
                db.execute("INSERT OR IGNORE INTO b1_agent_requesters VALUES(?,?,?)",
                           (row["job_id"], operator_id, self.clock()))
                return self._view(db, row)
        return None

    def enqueue(self, snapshot: FrozenSnapshot, cache_key, operator_id, execution=None):
        now = self.clock()
        with self.connection(write=True) as db:
            self._expire(db, now)
            context = snapshot.data.get('incident_context')
            if context is not None:
                current = db.execute("SELECT value FROM metadata WHERE key='incident_log_epoch'").fetchone()
                if context['log_epoch'] != (int(current[0]) if current else 0):
                    raise AgentError('analysis_data_changed', 'Журнал очищен во время запроса. Запросите анализ заново.', 409)
            cached = self._cached(db, cache_key)
            if cached:
                db.execute("INSERT OR IGNORE INTO b1_agent_requesters VALUES(?,?,?)", (cached["job_id"], operator_id, now))
                return self._view(db, cached), True
            queued = db.execute("SELECT queued_at FROM b1_agent_jobs WHERE status='queued' ORDER BY queued_at").fetchall()
            running = db.execute("SELECT started_at FROM b1_agent_jobs WHERE status='running'").fetchone()
            if len(queued) >= self.config.max_queued:
                raise AgentError("queue_full", "Two analyses are already waiting.", 429)
            remaining = max(0., self.config.max_execution_seconds - (now - running["started_at"])) if running else 0.
            estimate = remaining + len(queued) * self.config.max_execution_seconds
            if estimate >= self.config.max_queue_wait_seconds:
                raise AgentError("queue_busy", "Estimated wait cannot fit strictly inside the queue budget.", 429,
                                 {"estimated_wait_seconds": round(estimate, 3)})
            job_id = "aj-" + uuid.uuid4().hex
            db.execute("INSERT INTO b1_agent_jobs(job_id,cache_key,incident_id,snapshot_json,status,queued_at,queue_deadline,execution_json) "
                       "VALUES(?,?,?,?, 'queued',?,?,?)", (job_id, cache_key, snapshot.incident_id, canonical(snapshot.export()),
                       now, now + self.config.max_queue_wait_seconds, canonical(execution or {})))
            db.execute("INSERT INTO b1_agent_requesters VALUES(?,?,?)", (job_id, operator_id, now))
            return self._view(db, db.execute("SELECT * FROM b1_agent_jobs WHERE job_id=?", (job_id,)).fetchone()), False

    def recover(self):
        with self.connection(write=True) as db:
            now = self.clock()
            db.execute("UPDATE b1_agent_jobs SET status='failed',completed_at=?,error_json=? WHERE status='running'",
                       (now, canonical(AgentError("interrupted", "Worker stopped before completing analysis.").as_dict())))
            self._expire(db, now)

    def take_next(self):
        with self.connection(write=True) as db:
            now = self.clock()
            self._expire(db, now)
            if db.execute("SELECT 1 FROM b1_agent_jobs WHERE status='running'").fetchone():
                return None
            row = db.execute("SELECT * FROM b1_agent_jobs WHERE status='queued' ORDER BY queued_at,rowid LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE b1_agent_jobs SET status='running',started_at=? WHERE job_id=?", (now, row["job_id"]))
            return row["job_id"], FrozenSnapshot(json.loads(row["snapshot_json"])), json.loads(row["execution_json"])

    def finish(self, job_id, result=None, error=None):
        with self.connection(write=True) as db:
            db.execute("UPDATE b1_agent_jobs SET status=?,completed_at=?,result_json=?,error_json=? "
                       "WHERE job_id=? AND status='running'",
                       ("failed" if error else "completed", self.clock(), canonical(result) if result else None,
                        canonical(error) if error else None, job_id))

    def get(self, job_id):
        with self.connection(write=True) as db:
            self._expire(db, self.clock())
            row = db.execute("SELECT * FROM b1_agent_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise AgentError("not_found", "Agent job does not exist.", 404)
            return self._view(db, row)

    def _view(self, db, row):
        requesters = [r[0] for r in db.execute("SELECT operator_id FROM b1_agent_requesters WHERE job_id=? "
                                            "ORDER BY requested_at,rowid", (row["job_id"],))]
        snapshot = FrozenSnapshot(json.loads(row["snapshot_json"]))
        return {"job_id": row["job_id"], "incident_id": row["incident_id"], "status": row["status"],
                "queued_at": timestamp(row["queued_at"]), "started_at": timestamp(row["started_at"]),
                "queue_deadline": timestamp(row["queue_deadline"]), "execution": json.loads(row["execution_json"]),
                "completed_at": timestamp(row["completed_at"]), "requested_by_operator_id": requesters[0],
                "requested_by_operator_ids": requesters, "incident_snapshot": snapshot.descriptor(),
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "error": json.loads(row["error_json"]) if row["error_json"] else None}


class WorkerLease:
    """Refuse multiple processes sharing a job database, including accidental uvicorn workers."""
    def __init__(self, database):
        self.path = Path(str(database) + ".worker.lock")
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            import os
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise AgentError("worker_already_running", "Use one backend worker for the shared local agent.", 503) from exc

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
