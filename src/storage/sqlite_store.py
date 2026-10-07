"""Bounded local SQLite storage. Raw events and dispatch history are append-only."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, normalized TEXT NOT NULL,
                    event_time TEXT NOT NULL, received_at TEXT NOT NULL,
                    sensor_id TEXT NOT NULL, type TEXT NOT NULL,
                    asset_id TEXT, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_history
                    ON events(asset_id,event_time,event_id);
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY, condition_key TEXT,
                    body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS incident_condition ON incidents(condition_key);
                CREATE TABLE IF NOT EXISTS sensor_state (
                    sensor_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS asset_state (
                    asset_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_history (
                    history_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    incident_id TEXT NOT NULL, operator_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    normalized TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(incident_id,operator_id,request_id));
                CREATE TABLE IF NOT EXISTS transfers (
                    transfer_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT UNIQUE NOT NULL,
                    incident_id TEXT NOT NULL, recipient_operator_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS notification_recipient
                    ON notifications(recipient_operator_id,seq);
                CREATE TABLE IF NOT EXISTS presence (
                    operator_id TEXT NOT NULL, session_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(operator_id,session_id));
                CREATE TABLE IF NOT EXISTS model_observations (
                    observation_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL,
                    window_end TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA busy_timeout=5000")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @contextmanager
    def read(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN")
                yield db
            finally:
                db.rollback()
                db.close()
