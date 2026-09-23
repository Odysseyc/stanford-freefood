"""SQLite caches.

Two things are cached, for two different reasons:

  verdicts  -- keyed by content hash, so an event whose text hasn't changed
               never costs a second model call. This is what keeps the running
               cost near zero.
  http      -- keyed by URL, so the per-event CampusGroups detail fetches don't
               re-hit the site every three hours for events that never change.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts (
    content_hash TEXT PRIMARY KEY,
    payload      TEXT NOT NULL,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS http (
    url        TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- classifier verdicts ------------------------------------------------
    def get_verdict(self, content_hash: str) -> dict | None:
        row = self.db.execute(
            "SELECT payload FROM verdicts WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put_verdict(self, content_hash: str, payload: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?)",
            (content_hash, json.dumps(payload), time.time()),
        )
        self.db.commit()

    # -- http bodies --------------------------------------------------------
    def get_http(self, url: str, max_age: float) -> str | None:
        row = self.db.execute(
            "SELECT body, fetched_at FROM http WHERE url = ?", (url,)
        ).fetchone()
        if not row or time.time() - row[1] > max_age:
            return None
        return row[0]

    def put_http(self, url: str, body: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO http VALUES (?, ?, ?)", (url, body, time.time())
        )
        self.db.commit()

    def vacuum_old(self, max_age: float = 30 * 86400) -> None:
        cutoff = time.time() - max_age
        self.db.execute("DELETE FROM verdicts WHERE created_at < ?", (cutoff,))
        self.db.execute("DELETE FROM http WHERE fetched_at < ?", (cutoff,))
        self.db.commit()
