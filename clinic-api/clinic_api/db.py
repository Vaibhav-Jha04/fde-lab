from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    reason TEXT NOT NULL,
    starts_at TEXT NOT NULL,          -- clinic local time, YYYY-MM-DDTHH:MM
    status TEXT NOT NULL DEFAULT 'booked',
    created_at TEXT NOT NULL
);
-- The database itself refuses a second booking for the same slot, even if two calls race.
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot
    ON appointments(starts_at) WHERE status = 'booked';

CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    status TEXT,
    call_length REAL,
    summary TEXT,
    transcript TEXT,
    dispositions TEXT,
    outcome TEXT,
    payload TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_call(self, row: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO calls (call_id, received_at, status, call_length, summary, transcript, payload)
                VALUES (:call_id, :received_at, :status, :call_length, :summary, :transcript, :payload)
                ON CONFLICT(call_id) DO UPDATE SET
                    received_at = excluded.received_at,
                    status = excluded.status,
                    call_length = excluded.call_length,
                    summary = excluded.summary,
                    transcript = excluded.transcript,
                    payload = excluded.payload
                """,
                row,
            )

    def add_dispositions(self, call_id: str, received_at: str, dispositions: list) -> None:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT dispositions FROM calls WHERE call_id = ?", (call_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO calls (call_id, received_at, dispositions, payload) VALUES (?, ?, ?, '{}')",
                    (call_id, received_at, json.dumps(dispositions)),
                )
                return
            merged = {d.get("disposition_key"): d for d in json.loads(existing["dispositions"] or "[]")}
            merged.update({d.get("disposition_key"): d for d in dispositions})
            conn.execute(
                "UPDATE calls SET dispositions = ? WHERE call_id = ?",
                (json.dumps(list(merged.values())), call_id),
            )
