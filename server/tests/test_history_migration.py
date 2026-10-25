"""旧手机片段只在有对应打开信号时补回 App 明细。"""

import sqlite3

from presence.db import DB, MIGRATIONS


def test_backfill_phone_history_and_reopen(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    previous_version = next(i for i, sql in enumerate(MIGRATIONS) if "WITH phone AS" in sql)
    for sql in MIGRATIONS[:previous_version]:
        conn.executescript(sql)
    conn.execute(f"PRAGMA user_version = {previous_version}")
    conn.executemany(
        "INSERT INTO segments (id, line, category, start, end, source) VALUES (?, 'activity', 'phone', ?, ?, ?)",
        [("known", 100, 160, "iphone"), ("missing", 200, 260, "iphone")],
    )
    conn.executemany(
        "INSERT INTO signals (source, kind, ts, ttl, data_json, received_at) VALUES (?, 'phone_app', 100, 1800, ?, 100)",
        [("iphone", '{"app":"X","event":"open"}'),
         ("iphone", '{"app":"X","event":"close"}'),
         ("mac", '{"app":"Safari","event":"open"}')],
    )
    conn.commit()
    conn.close()

    for _ in range(2):
        db = DB(path)
        rows = db.media_history("app", 0, 300)
        assert [(r["key"], r["source"], r["seconds"]) for r in rows] == [("X", "iphone", 60)]
        assert len(db.segments_between(0, 300)) == 2
        db.close()
