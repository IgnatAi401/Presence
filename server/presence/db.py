"""SQLite 访问与迁移。时间统一存 UTC 时间戳（秒）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .engine import Course, Interval, ManualState

MIGRATIONS = [
    """
    CREATE TABLE signals (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        kind TEXT NOT NULL,
        ts REAL NOT NULL,
        ttl REAL NOT NULL,
        data_json TEXT NOT NULL,
        received_at REAL NOT NULL
    );
    CREATE INDEX signals_ts ON signals (ts);

    CREATE TABLE segments (
        id TEXT PRIMARY KEY,
        line TEXT NOT NULL,              -- anchor / activity
        start REAL NOT NULL,
        end REAL,                        -- NULL 表示仍在进行
        category TEXT NOT NULL,
        tier INTEGER,
        detail TEXT,
        project TEXT,
        course TEXT,
        domain TEXT,
        source TEXT,
        slacking INTEGER NOT NULL DEFAULT 0,
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX segments_start ON segments (start);
    CREATE INDEX segments_end ON segments (end);

    CREATE TABLE manual_states (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        text TEXT,
        tier INTEGER,
        start REAL NOT NULL,
        expires_at REAL NOT NULL,
        ended_at REAL
    );

    CREATE TABLE courses_cache (
        event_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        start REAL NOT NULL,
        end REAL NOT NULL,
        location TEXT
    );

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
]


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            self.conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")

    def close(self) -> None:
        self.conn.close()

    # -- signals

    def insert_signals(self, rows: list[tuple[str, str, float, float, dict]], received_at: float) -> None:
        self.conn.executemany(
            "INSERT INTO signals (source, kind, ts, ttl, data_json, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(s, k, ts, ttl, json.dumps(d, ensure_ascii=False), received_at) for s, k, ts, ttl, d in rows],
        )

    def last_signal_ts(self) -> float | None:
        return self.conn.execute("SELECT MAX(ts) FROM signals").fetchone()[0]

    def prune_signals(self, before: float) -> int:
        return self.conn.execute("DELETE FROM signals WHERE ts < ?", (before,)).rowcount

    # -- segments

    def save_intervals(self, ivs: list[Interval]) -> None:
        if not ivs:
            return
        self.conn.executemany(
            """
            INSERT INTO segments (id, line, start, end, category, tier, detail, project, course, domain,
                                  source, slacking, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET end = excluded.end, project = excluded.project,
                                           detail = excluded.detail, meta_json = excluded.meta_json
            """,
            [
                (iv.id, iv.line, iv.start, iv.end, iv.category, iv.tier, iv.detail, iv.project, iv.course,
                 iv.domain, iv.source, int(iv.slacking), json.dumps(iv.meta, ensure_ascii=False))
                for iv in ivs
            ],
        )

    def close_dangling(self, at: float) -> int:
        """启动时：上次没结束的片段截止到最后一条信号的时间，停机时间不算进任何类别。"""
        return self.conn.execute(
            "UPDATE segments SET end = MAX(start, ?) WHERE end IS NULL", (at,)
        ).rowcount

    def segments_between(self, start: float, end: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM segments WHERE start < ? AND (end IS NULL OR end > ?) ORDER BY start",
            (end, start),
        ).fetchall()

    # -- manual_states

    def save_manual(self, states: list[ManualState]) -> None:
        self.conn.executemany(
            """
            INSERT INTO manual_states (id, state, text, tier, start, expires_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET ended_at = excluded.ended_at
            """,
            [(m.id, m.state, m.text, m.tier, m.start, m.expires_at, m.ended_at) for m in states],
        )

    def active_manual(self, now: float) -> list[ManualState]:
        rows = self.conn.execute(
            "SELECT * FROM manual_states WHERE ended_at IS NULL AND expires_at > ? ORDER BY start", (now,)
        ).fetchall()
        return [
            ManualState(r["state"], r["start"], r["expires_at"], text=r["text"], tier=r["tier"], id=r["id"])
            for r in rows
        ]

    def end_stale_manual(self, now: float) -> None:
        self.conn.execute(
            "UPDATE manual_states SET ended_at = expires_at WHERE ended_at IS NULL AND expires_at <= ?", (now,)
        )

    # -- courses_cache

    def save_courses(self, courses: list[Course], keep_from: float) -> None:
        self.conn.execute("BEGIN")
        self.conn.execute("DELETE FROM courses_cache")
        self.conn.executemany(
            "INSERT INTO courses_cache (event_id, title, start, end, location) VALUES (?, ?, ?, ?, ?)",
            [(c.event_id, c.title, c.start, c.end, c.location) for c in courses if c.end >= keep_from],
        )
        self.conn.execute("COMMIT")

    def load_courses(self) -> list[Course]:
        rows = self.conn.execute("SELECT * FROM courses_cache ORDER BY start").fetchall()
        return [Course(r["event_id"], r["title"], r["start"], r["end"], r["location"]) for r in rows]
