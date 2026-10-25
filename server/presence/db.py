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
    """
    CREATE TABLE media_cache (
        id TEXT PRIMARY KEY,             -- 图片内容的 sha256（十六进制小写）
        mime TEXT NOT NULL,
        path TEXT NOT NULL,              -- 相对媒体目录的文件名
        origin_url TEXT,                 -- 从网上抓取时的原地址；设备上传的为 NULL
        source TEXT,
        size INTEGER NOT NULL,
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE TABLE video_meta (
        site TEXT NOT NULL,              -- bilibili / youtube
        id TEXT NOT NULL,                -- BV 号、番剧 ep / ss 号、YouTube 视频 ID
        title TEXT,                      -- NULL 表示上次没查到，过一段时间再查
        owner TEXT,
        url TEXT,
        cover_hash TEXT,                 -- media_cache 里封面的 sha256
        parts_json TEXT NOT NULL DEFAULT '[]',
        fetched_at REAL NOT NULL,
        PRIMARY KEY (site, id)
    );
    """,
    """
    CREATE TABLE site_icons (
        domain TEXT PRIMARY KEY,         -- Safari 网页的实际域名，例如 www.zhihu.com
        cover_hash TEXT,                 -- media_cache 里图标的 sha256；NULL 表示没取到，过一段时间再取
        fetched_at REAL NOT NULL
    );
    """,
    # 图标来源从 Google 改成网站自己声明的图标：清空旧结果，下次用到时重新取
    "DELETE FROM site_icons;",
    """
    CREATE TABLE media_log (
        id TEXT PRIMARY KEY,             -- 对应 segments 里同一段的 id
        type TEXT NOT NULL,              -- game / music / video / app
        key TEXT NOT NULL,               -- 汇总用：游戏是 Steam appid（非 Steam 用进程名或游戏名），歌是“歌手 - 歌名”，
                                         -- 视频是“网站:视频 ID”，例如 bilibili:BV1xx411c7mD
        title TEXT,                      -- 视频标题查到后才补上，可能暂时为 NULL
        artist_or_owner TEXT,            -- 歌手；视频的 UP 主 / 频道
        album TEXT,
        player TEXT,                     -- 播放器：aimp / apple_music；视频是网站 bilibili / youtube；游戏为 NULL
        page INTEGER,                    -- 视频的分 P（第几集），没有为 NULL
        play TEXT NOT NULL,              -- 同一次播放：暂停后在余温内继续，几段共用一个值
        start REAL NOT NULL,
        end REAL,                        -- NULL 表示仍在进行
        source TEXT                      -- 设备：mac / windows / iphone / steam
    );
    CREATE INDEX media_log_start ON media_log (start);
    CREATE INDEX media_log_key ON media_log (type, key);
    -- 补上建表前已有的游戏片段
    INSERT INTO media_log (id, type, key, title, play, start, end, source)
    SELECT id, 'game', detail, detail, id, start, end, source
    FROM segments WHERE line = 'activity' AND category = 'game' AND detail IS NOT NULL;
    """,
    """
    -- 旧手机片段没有保存 App 名，从仍保留的打开信号中恢复；不猜测缺失数据。
    WITH phone AS (
        SELECT s.*, (
            SELECT json_extract(data_json, '$.app') FROM signals
            WHERE source = s.source AND kind = 'phone_app' AND ts = s.start
              AND COALESCE(json_extract(data_json, '$.event'), 'open') != 'close'
              AND json_type(data_json, '$.app') = 'text'
            ORDER BY id DESC LIMIT 1
        ) AS app
        FROM segments s WHERE line = 'activity' AND category = 'phone'
    )
    INSERT INTO media_log (id, type, key, title, play, start, end, source)
    SELECT id, 'app', app, app, id, start, end, source FROM phone WHERE app != '';
    """,
    """
    ALTER TABLE media_log ADD COLUMN private INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE web_log (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        domain TEXT NOT NULL,
        title TEXT,
        private INTEGER NOT NULL DEFAULT 0,
        start REAL NOT NULL,
        end REAL,
        last_seen REAL NOT NULL,
        expires REAL NOT NULL
    );
    CREATE INDEX web_log_start ON web_log (start);
    CREATE UNIQUE INDEX web_log_open ON web_log (source) WHERE end IS NULL;
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
        self.conn.execute("UPDATE web_log SET end = MAX(start, last_seen) WHERE end IS NULL")
        self.conn.execute("UPDATE media_log SET end = MAX(start, ?) WHERE end IS NULL", (at,))
        return self.conn.execute(
            "UPDATE segments SET end = MAX(start, ?) WHERE end IS NULL", (at,)
        ).rowcount

    def segments_between(self, start: float, end: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM segments WHERE start < ? AND (end IS NULL OR end > ?) ORDER BY start",
            (end, start),
        ).fetchall()

    # -- web_log（独立于活动分类，换网站 / 标题立即拆段，长期保留）

    def expire_web(self, now: float) -> None:
        self.conn.execute("UPDATE web_log SET end = MAX(start, last_seen) WHERE end IS NULL AND expires < ?", (now,))

    def record_web(self, source: str, kind: str, ts: float, ttl: float, data: dict, away_sec: float) -> None:
        if kind not in ("desktop", "heartbeat"):
            return
        row = self.conn.execute("SELECT * FROM web_log WHERE source = ? AND end IS NULL", (source,)).fetchone()
        if row is not None and ts < row["last_seen"]:
            return  # 补发的旧信号不能覆盖较新的浏览记录
        if row is not None and ts > row["expires"]:
            self.conn.execute("UPDATE web_log SET end = MAX(start, last_seen) WHERE id = ?", (row["id"],))
            row = None
        if kind == "heartbeat":
            if row is not None:
                self.conn.execute("UPDATE web_log SET last_seen = ?, expires = ? WHERE id = ?",
                                  (ts, ts + ttl, row["id"]))
            return
        idle = float(data.get("idle_sec") or 0)
        active = not data.get("locked") and idle < away_sec
        domain = data.get("domain") if data.get("bundle_id") == "com.apple.Safari" and active else None
        domain = domain.lower() if isinstance(domain, str) and domain else None
        title = data.get("title")
        title = title[:200] if isinstance(title, str) and title else None
        private = bool(data.get("private") or data.get("hidden"))
        if row is not None:
            if domain and (row["domain"], row["title"], row["private"]) == (domain, title, private):
                self.conn.execute("UPDATE web_log SET last_seen = ?, expires = ? WHERE id = ?",
                                  (ts, ts + ttl, row["id"]))
                return
            end = ts - idle if idle >= away_sec and not data.get("locked") else ts
            self.conn.execute("UPDATE web_log SET end = MAX(start, ?) WHERE id = ?", (end, row["id"]))
        if domain:
            self.conn.execute(
                "INSERT INTO web_log (source, domain, title, private, start, last_seen, expires) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source, domain, title, int(private), ts, ts, ts + ttl),
            )

    def web_history(self, start: float, end: float) -> list[sqlite3.Row]:
        """仅供私有查询；时间范围内的浏览明细，保留真实开始 / 结束时间。"""
        return self.conn.execute(
            "SELECT id, source, domain, title, private, start, end FROM web_log "
            "WHERE start < ? AND (end IS NULL OR end > ?) ORDER BY start DESC, id DESC",
            (end, start),
        ).fetchall()

    # -- media_log

    def save_media_log(self, ivs: list[Interval]) -> None:
        """游戏、听歌、看视频和手机 App 片段同步记入历史，保留设备来源。"""
        rows = []
        for iv in ivs:
            m = iv.meta.get("media")
            if m:
                rows.append((iv.id, m["type"], m["key"], m.get("title"), m.get("artist"), m.get("album"),
                             m.get("player") or m.get("site"), m.get("page"), m["play"], iv.start, iv.end, iv.source, int(bool(iv.meta.get("private")))))
        self.conn.executemany(
            """
            INSERT INTO media_log (id, type, key, title, artist_or_owner, album, player, page, play, start, end, source, private)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET end = excluded.end,
                title = COALESCE(excluded.title, title), artist_or_owner = COALESCE(excluded.artist_or_owner, artist_or_owner)
            """,
            rows,
        )

    def fill_video_log(self, site: str, vid: str, title: str, owner: str | None) -> None:
        """视频信息查到后，补上之前没有标题的观看记录。"""
        self.conn.execute(
            "UPDATE media_log SET title = ?, artist_or_owner = ? WHERE type = 'video' AND key = ? AND title IS NULL",
            (title, owner, f"{site}:{vid}"),
        )

    def media_totals(self, type_: str, start: float, end: float) -> list[sqlite3.Row]:
        """[start, end) 内按内容和设备分别累计秒数、次数，按时长从多到少。"""
        return self.conn.execute(
            """
            SELECT key, source, MAX(title) AS title, MAX(artist_or_owner) AS artist,
                   SUM(MIN(COALESCE(end, ?), ?) - MAX(start, ?)) AS seconds, COUNT(DISTINCT play) AS plays
            FROM media_log
            WHERE type = ? AND start < ? AND (end IS NULL OR end > ?)
            GROUP BY key, source ORDER BY seconds DESC
            """,
            (end, end, start, type_, end, start),
        ).fetchall()

    def media_history(self, type_: str, start: float, end: float) -> list[sqlite3.Row]:
        """[start, end) 内的播放记录，按开始时间倒序；暂停拆开的几段合成一次播放。"""
        return self.conn.execute(
            """
            SELECT play, key, MAX(private) AS private, MAX(title) AS title, MAX(artist_or_owner) AS artist, MAX(album) AS album,
                   MAX(player) AS player, MAX(page) AS page, MAX(source) AS source, MIN(start) AS start,
                   CASE WHEN COUNT(end) = COUNT(*) THEN MAX(end) END AS end,
                   SUM(COALESCE(end, ?) - start) AS seconds
            FROM media_log
            WHERE type = ? AND start < ? AND (end IS NULL OR end > ?)
            GROUP BY play, source ORDER BY start DESC
            """,
            (end, type_, end, start),
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

    # -- media_cache

    def get_media(self, media_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM media_cache WHERE id = ?", (media_id,)).fetchone()

    def add_media(self, media_id: str, mime: str, path: str, size: int, source: str | None,
                  created_at: float, origin_url: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO media_cache (id, mime, path, origin_url, source, size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING
            """,
            (media_id, mime, path, origin_url, source, size, created_at),
        )

    # -- video_meta

    def media_by_origin(self, url: str) -> str | None:
        row = self.conn.execute("SELECT id FROM media_cache WHERE origin_url = ? ORDER BY created_at DESC LIMIT 1",
                                (url,)).fetchone()
        return row["id"] if row else None

    def get_video_meta(self, site: str, vid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM video_meta WHERE site = ? AND id = ?", (site, vid)).fetchone()

    def save_video_meta(self, site: str, vid: str, fetched_at: float, title: str | None = None,
                        owner: str | None = None, url: str | None = None, cover_hash: str | None = None,
                        parts: list[str] | None = None) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO video_meta (site, id, title, owner, url, cover_hash, parts_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (site, vid, title, owner, url, cover_hash, json.dumps(parts or [], ensure_ascii=False), fetched_at),
        )

    # -- site_icons

    def get_site_icon(self, domain: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM site_icons WHERE domain = ?", (domain,)).fetchone()

    def save_site_icon(self, domain: str, cover_hash: str | None, fetched_at: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO site_icons (domain, cover_hash, fetched_at) VALUES (?, ?, ?)",
            (domain, cover_hash, fetched_at),
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
