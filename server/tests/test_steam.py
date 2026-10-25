"""Steam 轮询测试：替换查询函数，不联网。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from presence.config import Settings, SteamSettings
from presence.main import create_app
from presence.sources import steam
from presence.sources.steam import Playing, SteamError, parse

from test_api import RULES, T0, Clock

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def test_parse():
    assert parse({"response": {"players": [{"steamid": "1"}]}}) is None
    g = parse({"response": {"players": [{"gameid": "1145360", "gameextrainfo": "Hades"}]}})
    assert g == Playing("Hades", "1145360")
    # 非 Steam 游戏快捷方式：没有商店页
    g = parse({"response": {"players": [{"gameid": "13286191472603283456", "gameextrainfo": "Minecraft"}]}})
    assert g == Playing("Minecraft", None)
    with pytest.raises(SteamError):
        parse({"response": {"players": []}})


@pytest.fixture
def env(tmp_path):
    settings = Settings(devices={}, db_path=tmp_path / "p.db", steam=SteamSettings(api_key="k", steam_id="7656"))
    clock = Clock(T0)
    app = create_app(settings, RULES, clock=clock, background=False)
    with TestClient(app) as client:
        p = client.app.state.presence
        state = {"game": None, "fail": False, "covers": 0}

        def fetch(key, sid):
            if state["fail"]:
                raise SteamError("网络失败")
            return state["game"]

        def cover(url, limit):
            state["covers"] += 1
            return JPEG

        p.fetch_steam = fetch
        p.fetch_cover = cover
        yield client, clock, p, state


def poll(client, p):
    async def run():
        delay = await p.poll_steam()
        await asyncio.gather(*p.tasks)
        return delay
    return client.portal.call(run)


def test_steam_start_and_stop(env):
    client, clock, p, state = env
    assert poll(client, p) == 60
    state["game"] = Playing("Hades", "1145360")
    assert poll(client, p) == 30
    clock.t += 30
    poll(client, p)
    now = client.get("/presence/api/now").json()
    assert now["main"]["category"] == "game"
    assert now["main"]["label"] == "玩《Hades》"
    card = now["main"]["card"]
    assert card["appid"] == "1145360"
    assert card["cover"].startswith(p.settings.media_base + "/")
    assert state["covers"] == 1  # 封面只取一次

    # 查询失败时不算退出
    state["fail"] = True
    clock.t += 30
    assert poll(client, p) == 30
    assert client.get("/presence/api/now").json()["main"]["category"] == "game"

    state["fail"], state["game"] = False, None
    clock.t += 30
    assert poll(client, p) == 60
    clock.t += 200  # 退出后余温 3 分钟
    p.update()
    assert client.get("/presence/api/now").json()["main"]["category"] != "game"


def test_steam_switch_game(env):
    client, clock, p, state = env
    state["game"] = Playing("Hades", "1145360")
    poll(client, p)
    clock.t += 60
    state["game"] = Playing("Celeste", "504230")
    poll(client, p)
    clock.t += 30
    poll(client, p)
    now = client.get("/presence/api/now").json()
    games = [i for i in [now["main"], *now["side"]] if i["category"] == "game"]
    assert [g["card"]["name"] for g in games] == ["Celeste"]


def test_cover_url():
    assert steam.cover_url("10").endswith("/apps/10/header.jpg")


def test_media_log_records_game_time(env):
    client, clock, p, state = env
    start = clock.t
    def play(seconds):  # 每 30 秒查一次，和真实轮询一样
        poll(client, p)
        for _ in range(seconds // 30):
            clock.t += 30
            poll(client, p)

    state["game"] = Playing("Hades", "1145360")
    play(570)
    clock.t += 30
    state["game"] = Playing("Celeste", "504230")
    play(300)

    # 进行中的游戏算到查询的截止时间
    rows = [dict(r) for r in p.db.media_totals("game", start, clock.t)]
    assert rows == [
        {"key": "1145360", "title": "Hades", "artist": None, "seconds": 600, "plays": 1},
        {"key": "504230", "title": "Celeste", "artist": None, "seconds": 300, "plays": 1},
    ]

    state["game"] = None
    poll(client, p)
    clock.t += 1000
    p.update()
    log = p.db.conn.execute("SELECT * FROM media_log ORDER BY start").fetchall()
    assert [(r["type"], r["source"], r["end"] - r["start"]) for r in log] == [("game", "steam", 600), ("game", "steam", 300)]
    assert dict(p.db.media_totals("game", start, clock.t)[1])["seconds"] == 300  # 退出后不再增加


def test_media_log_dangling_closed_on_restart(tmp_path):
    from presence.db import DB  # noqa: PLC0415
    from presence.engine import Interval  # noqa: PLC0415

    db = DB(tmp_path / "p.db")
    iv = Interval(line="activity", category="game", start=100, source="windows", detail="Minecraft",
                  meta={"media": {"type": "game", "key": "javaw.exe", "title": "Minecraft", "play": "p1"}})
    db.save_intervals([iv])
    db.save_media_log([iv])
    db.conn.execute("INSERT INTO signals (source, kind, ts, ttl, data_json, received_at) VALUES ('windows', 'game', 400, 60, '{}', 400)")
    db.close_dangling(db.last_signal_ts())
    assert db.conn.execute("SELECT end FROM media_log").fetchone()[0] == 400
