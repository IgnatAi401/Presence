"""第 4 期：公开历史、私有回看接口、网关密钥和每日备份。"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from presence.config import Settings
from presence.main import create_app
from presence.stats import date_bounds, day_bounds

from test_api import PHONE_TOKEN, RULES, T0, TOKEN, WIN_TOKEN, Clock, post

GATE = "gate-secret-for-tests"
OK = {"x-ignat-gate": GATE}
DAY = day_bounds(T0, Settings(devices={}).tz, 4)[2]  # T0 所在的那天


@pytest.fixture
def env(tmp_path):
    settings = Settings(devices={"mac": TOKEN, "windows": WIN_TOKEN, "iphone": PHONE_TOKEN},
                        db_path=tmp_path / "p.db", gate_secret=GATE)
    clock = Clock(T0)
    with TestClient(create_app(settings, RULES, clock=clock, background=False)) as client:
        p = client.app.state.presence
        p.fetch_video_meta = lambda site, vid: None
        p.fetch_icon = lambda domain, limit: None
        yield client, clock, settings


def research_then_video(client, clock):
    """开发 10 分钟 → 切到 B站（摸鱼）6 分 40 秒；前 10 分钟 Windows 上在放歌。"""
    song = {"player": "aimp", "title": "歌", "artist": "人", "position": 0, "duration": 300}
    post(client, [{"kind": "desktop", "ts": T0, "ttl": 3600,
                   "data": {"bundle_id": "com.microsoft.VSCode", "project": "Presence", "category": "dev"}}])
    post(client, [{"kind": "music", "ts": T0, "ttl": 1800, "data": {**song, "state": "playing"}}], token=WIN_TOKEN)
    clock.t = T0 + 600
    post(client, [{"kind": "desktop", "ts": T0 + 600, "ttl": 3600,
                   "data": {"bundle_id": "com.apple.Safari", "domain": "www.bilibili.com", "title": "某视频"}}])
    post(client, [{"kind": "music", "ts": T0 + 600, "ttl": 1800, "data": {**song, "state": "paused"}}], token=WIN_TOKEN)
    clock.t = T0 + 1000
    post(client, [{"kind": "heartbeat", "ts": T0 + 1000}])


# -- 网关密钥


@pytest.mark.parametrize("path", [
    f"/presence/api/private/day?date={DAY}",
    "/presence/api/private/focus",
    "/presence/api/%70rivate/focus",
    "/presence/api//private/focus",
])
def test_private_needs_gate_secret(env, path):
    client, *_ = env
    for headers in ({}, {"x-ignat-gate": "guess"}, {"x-ignat-gate": GATE + "x"}):
        r = client.get(path, headers=headers)
        assert r.status_code == 404, (path, headers)
        assert "days" not in r.text


def test_encoded_private_path_with_gate(env):
    client, *_ = env
    assert client.get(f"/presence/api/private/focus?from={DAY}&to={DAY}", headers=OK).json()["days"]
    assert client.get(f"/presence/api/%70rivate/focus?from={DAY}&to={DAY}", headers=OK).status_code == 200


def test_private_closed_without_configured_secret(tmp_path):
    settings = Settings(devices={"mac": TOKEN}, db_path=tmp_path / "p.db")
    with TestClient(create_app(settings, RULES, clock=Clock(T0), background=False)) as client:
        assert client.get("/presence/api/private/focus", headers={"x-ignat-gate": ""}).status_code == 404


def test_dev_private_only_for_requests_outside_cloudflare(tmp_path):
    settings = Settings(devices={"mac": TOKEN}, db_path=tmp_path / "p.db", dev_private=True)
    with TestClient(create_app(settings, RULES, clock=Clock(T0), background=False)) as client:
        assert client.get("/presence/api/private/focus").status_code == 200
        r = client.get("/presence/api/private/focus", headers={"cf-connecting-ip": "203.0.113.1"})
        assert r.status_code == 404


def test_private_cors_allows_credentials_for_now_only(env):
    client, *_ = env
    r = client.get("/presence/api/private/focus", headers={**OK, "Origin": "https://now.ignat.ai"})
    assert r.headers["access-control-allow-origin"] == "https://now.ignat.ai"
    assert r.headers["access-control-allow-credentials"] == "true"
    r = client.get("/presence/api/private/focus", headers={**OK, "Origin": "https://ignat.ai"})
    assert "access-control-allow-origin" not in r.headers
    pre = client.options("/presence/api/private/focus", headers={
        "Origin": "https://now.ignat.ai", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-credentials"] == "true"
    # 公开接口不允许带 Cookie
    r = client.get("/presence/api/history", headers={"Origin": "https://now.ignat.ai"})
    assert "access-control-allow-credentials" not in r.headers


# -- 公开历史


def test_history_days_and_media_ranks(env):
    client, clock, _ = env
    research_then_video(client, clock)
    h = client.get(f"/presence/api/history?from={DAY}&to={DAY}").json()
    assert [d["date"] for d in h["days"]] == [DAY]
    d = h["days"][0]
    assert d["totals"]["activity"] == {"dev": 600, "video": 400, "music": 600}
    assert d["serious_sec"] == 600  # 摸鱼时段不算正事
    assert [(m["title"], m["artist"], m["plays"]) for m in h["music"]] == [("歌", "人", 1)]
    assert h["names"]["dev"] == "开发"
    # 公开历史不含项目名、网站和视频
    assert "Presence" not in str(h) and "bilibili" not in str(h) and "video" not in h


def test_history_skips_future_days_and_validates_range(env):
    client, *_ = env
    tomorrow = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    h = client.get(f"/presence/api/history?from={DAY}&to={tomorrow}").json()
    assert [d["date"] for d in h["days"]] == [DAY]
    assert len(client.get("/presence/api/history").json()["days"]) == 182  # 默认最近半年
    assert client.get("/presence/api/history?from=2026-13-01").status_code == 400
    assert client.get(f"/presence/api/history?from={tomorrow}&to={DAY}").status_code == 400
    assert client.get("/presence/api/history?from=2020-01-01&to=2026-01-01").status_code == 400


def test_public_day_matches_today(env):
    client, clock, _ = env
    research_then_video(client, clock)
    today = client.get("/presence/api/today").json()
    day = client.get(f"/presence/api/day?date={DAY}").json()
    assert day == today
    assert client.get("/presence/api/day?date=nope").status_code == 400


# -- 私有回看


def test_private_day_has_full_detail(env):
    client, clock, _ = env
    research_then_video(client, clock)
    d = client.get(f"/presence/api/private/day?date={DAY}", headers=OK).json()
    dev = [s for s in d["segments"] if s["category"] == "dev"]
    assert [(s["project"], s["slacking"]) for s in dev] == [("Presence", False), ("Presence", True)]
    assert [w["domain"] for w in d["web"]] == ["www.bilibili.com"]
    assert [(m["type"], m["title"]) for m in d["media"]] == [("music", "歌")]
    assert (d["start"], d["end"]) == date_bounds(date.fromisoformat(DAY), Settings(devices={}).tz, 4)


def test_private_summary_groups_and_during(env):
    client, clock, _ = env
    research_then_video(client, clock)
    q = f"/presence/api/private/summary?from={DAY}&to={DAY}"
    cats = client.get(q, headers=OK).json()["items"]
    assert {(i["line"], i["key"]): i["seconds"] for i in cats} == {
        ("activity", "dev"): 600, ("activity", "video"): 400, ("activity", "music"): 600}
    assert client.get(q + "&group=project", headers=OK).json()["items"] == [
        {"key": "Presence", "title": "Presence", "seconds": 600}]
    site = client.get(q + "&group=site", headers=OK).json()["items"]
    assert [(i["key"], i["title"], i["seconds"]) for i in site] == [("www.bilibili.com", "B站", 400)]
    track = client.get(q + "&group=track", headers=OK).json()["items"]
    assert [(i["title"], i["seconds"]) for i in track] == [("歌", 600)]
    assert client.get(q + "&group=nope", headers=OK).status_code == 400
    assert client.get(q + "&during=dev", headers=OK).status_code == 400


def test_summary_during_eat(env):
    """吃饭时在看什么：只统计吃饭那段时间里的活动。"""
    client, clock, _ = env
    research_then_video(client, clock)
    post(client, [{"kind": "manual", "ts": T0 + 800, "data": {"state": "eat"}}], token=PHONE_TOKEN)
    clock.t = T0 + 900
    post(client, [{"kind": "manual", "ts": T0 + 900, "data": {"state": "eat", "action": "stop"}}], token=PHONE_TOKEN)
    items = client.get(f"/presence/api/private/summary?from={DAY}&to={DAY}&during=eat", headers=OK).json()["items"]
    assert {i["key"]: i["seconds"] for i in items} == {"video": 100}  # 歌在 T0+600 已暂停


def test_focus(env):
    client, clock, _ = env
    research_then_video(client, clock)
    f = client.get(f"/presence/api/private/focus?from={DAY}&to={DAY}", headers=OK).json()
    day = f["days"][0]
    assert (day["focus_sec"], day["longest_sec"], day["interruptions"]) == (600, 600, 1)
    assert day["longest_start"] == T0
    assert day["slack_sec"] == 400
    assert f["channels"] == [{"category": "video", "title": "B站", "seconds": 400}]


def test_export_csv_and_json(env):
    client, clock, _ = env
    research_then_video(client, clock)
    r = client.get(f"/presence/api/private/export?from={DAY}&to={DAY}&format=csv", headers=OK)
    assert r.headers["content-type"].startswith("text/csv")
    assert f'presence-{DAY}_{DAY}.csv' in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("id,line,category,start,end")
    assert any(",dev," in ln and "Presence" in ln for ln in lines[1:])
    j = client.get(f"/presence/api/private/export?from={DAY}&to={DAY}", headers=OK).json()
    assert {"segments", "media", "web"} <= j.keys()
    assert j["web"][0]["domain"] == "www.bilibili.com"
    assert client.get("/presence/api/private/export?format=xml", headers=OK).status_code == 400


# -- 每日备份


def test_daily_backup_keeps_latest(env):
    client, clock, settings = env
    p = client.app.state.presence
    d = settings.db_path.parent / "backups"
    first = p.backup_if_due(clock.t)
    assert first.exists() and first.name == f"presence-{DAY.replace('-', '')}.db"
    assert p.backup_if_due(clock.t + 60) is None  # 同一天不重复备份
    for i in range(1, 32):
        p.backup_if_due(clock.t + i * 86400)
    backups = sorted(d.glob("presence-*.db"))
    assert len(backups) == settings.backup_keep
    assert first not in backups
    assert not list(d.glob(".*.tmp"))
