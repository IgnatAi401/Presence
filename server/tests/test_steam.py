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
    assert now["main"]["label"] == "正在玩《Hades》"
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
