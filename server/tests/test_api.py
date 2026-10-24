"""接口与统计测试：临时数据库 + 可控时钟。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presence.config import Settings
from presence.main import create_app
from presence.rules import Rules
from presence.stats import day_bounds

RULES = Rules.load(Path(__file__).resolve().parent.parent / "config" / "rules.yaml")
TOKEN = "test-token-mac"
T0 = 1_800_000_000.0  # 2027-01-15 16:00 北京时间


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    settings = Settings(devices={"mac": TOKEN}, db_path=tmp_path / "p.db")
    clock = Clock(T0)
    app = create_app(settings, RULES, clock=clock, background=False)
    with TestClient(app) as client:
        yield client, clock, settings


def post(client, signals, token=TOKEN):
    return client.post("/presence/api/signal", json={"signals": signals},
                       headers={"Authorization": f"Bearer {token}"})


def test_signal_requires_token(env):
    client, *_ = env
    assert post(client, [{"kind": "heartbeat"}], token="nope").status_code == 401
    assert client.post("/presence/api/signal", json={"kind": "heartbeat"}).status_code == 401


def test_signal_then_now(env):
    client, clock, _ = env
    r = post(client, [{"kind": "desktop", "ts": T0, "data": {"bundle_id": "com.mathworks.matlab"}}])
    assert r.json() == {"ok": True, "accepted": 1}
    clock.t += 60
    now = client.get("/presence/api/now").json()
    assert now["main"]["category"] == "research"
    assert now["main"]["today_sec"] == 60
    assert "project" not in now["main"]


def test_single_signal_body_and_future_ts_clamped(env):
    client, clock, _ = env
    r = client.post("/presence/api/signal", headers={"Authorization": f"Bearer {TOKEN}"},
                    json={"kind": "desktop", "ts": T0 + 9999, "data": {"bundle_id": "com.microsoft.VSCode"}})
    assert r.status_code == 200
    assert client.get("/presence/api/now").json()["main"]["since"] == T0


def test_today_timeline_and_totals(env):
    client, clock, _ = env
    clock.t = T0 + 600
    post(client, [
        {"kind": "desktop", "ts": T0, "ttl": 3600, "data": {"bundle_id": "com.mathworks.matlab"}},
        {"kind": "desktop", "ts": T0 + 600, "ttl": 3600, "data": {"bundle_id": "com.apple.Safari", "domain": "bilibili.com"}},
    ])
    clock.t = T0 + 1000
    client.get("/presence/api/now")  # 触发推进
    post(client, [{"kind": "heartbeat", "ts": T0 + 1000}])
    today = client.get("/presence/api/today").json()
    # 摸鱼时段主状态仍是科研；摸鱼时间不计入科研时长
    assert [(b["category"], b["start"] - T0, b["end"] - T0) for b in today["timeline"]] == [("research", 0, 1000)]
    assert today["totals"]["activity"] == {"research": 600, "video": 400}


def test_cors_public_only(env):
    client, *_ = env
    r = client.get("/presence/api/now", headers={"Origin": "https://now.ignat.ai"})
    assert r.headers["access-control-allow-origin"] == "https://now.ignat.ai"
    r = client.get("/presence/api/now", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = client.options("/presence/api/signal", headers={"Origin": "https://now.ignat.ai"})
    assert "access-control-allow-origin" not in r.headers


def test_rate_limit(env):
    client, clock, settings = env
    for _ in range(settings.rate_per_minute):
        assert post(client, [{"kind": "heartbeat"}]).status_code == 200
    assert post(client, [{"kind": "heartbeat"}]).status_code == 429
    clock.t += 61
    assert post(client, [{"kind": "heartbeat"}]).status_code == 200


def test_restart_truncates_open_segments_and_restores_manual(tmp_path):
    settings = Settings(devices={"mac": TOKEN, "iphone": "tok-iphone"}, db_path=tmp_path / "p.db")
    clock = Clock(T0)
    with TestClient(create_app(settings, RULES, clock=clock, background=False)) as client:
        post(client, [{"kind": "desktop", "ts": T0, "ttl": 3600, "data": {"bundle_id": "com.mathworks.matlab"}}])
        post(client, [{"kind": "manual", "ts": T0, "data": {"state": "eat"}}], token="tok-iphone")
        clock.t = T0 + 300
        post(client, [{"kind": "heartbeat", "ts": T0 + 300}])

    clock.t = T0 + 1200  # 停机 15 分钟后重启
    with TestClient(create_app(settings, RULES, clock=clock, background=False)) as client:
        now = client.get("/presence/api/now").json()
        assert now["main"]["category"] == "eat"  # 吃饭还没到期，恢复
        assert now["main"]["since"] == T0
        today = client.get("/presence/api/today").json()
        assert today["totals"]["activity"]["research"] == 300  # 停机时间不算进科研
        assert today["totals"]["anchor"]["eat"] == 300  # 停机期间不算，重启后从 T0+1200 重新计时


def test_day_bounds_4am():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    from datetime import datetime

    two_am = datetime(2026, 10, 25, 2, 0, tzinfo=tz).timestamp()
    start, end, date = day_bounds(two_am, tz, 4)
    assert date == "2026-10-24"
    assert datetime.fromtimestamp(start, tz).hour == 4 and end - start == 86400
