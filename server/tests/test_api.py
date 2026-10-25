"""接口与统计测试：临时数据库 + 可控时钟。"""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presence.config import Settings
from presence.sources.web import Icon
from presence.main import create_app
from presence.rules import Rules
from presence.stats import day_bounds

RULES = Rules.load(Path(__file__).resolve().parent.parent / "config" / "rules.yaml")
TOKEN = "test-token-mac"
WIN_TOKEN = "test-token-windows"
PHONE_TOKEN = "test-token-iphone"
T0 = 1_800_000_000.0  # 2027-01-15 16:00 北京时间


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    settings = Settings(devices={"mac": TOKEN, "windows": WIN_TOKEN, "iphone": PHONE_TOKEN}, db_path=tmp_path / "p.db")
    clock = Clock(T0)
    app = create_app(settings, RULES, clock=clock, background=False)
    with TestClient(app) as client:
        p = client.app.state.presence
        p.fetch_video_meta = lambda site, vid: None  # 测试不联网
        p.fetch_icon = lambda domain, limit: None
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


def test_office_main_carries_research_today(env):
    client, clock, _ = env
    post(client, [{"kind": "manual", "ts": T0, "data": {"state": "office"}},
                  {"kind": "desktop", "ts": T0, "data": {"bundle_id": "com.mathworks.matlab"}}])
    clock.t += 120
    main = client.get("/presence/api/now").json()["main"]
    assert (main["category"], main["activity"], main["today_sec"]) == ("office", "research", 120)


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


# -- 封面

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def put_media(client, data, media_id=None, token=TOKEN):
    media_id = media_id or hashlib.sha256(data).hexdigest()
    return client.put(f"/presence/api/media/{media_id}", content=data,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})


def test_media_upload_and_get(env):
    client, _, settings = env
    h = hashlib.sha256(PNG).hexdigest()
    assert client.head(f"/presence/api/media/{h}").status_code == 404
    r = put_media(client, PNG)
    assert (r.status_code, r.json()) == (201, {"ok": True, "created": True})
    assert (settings.media_dir / f"{h}.png").read_bytes() == PNG
    r = client.get(f"/presence/api/media/{h}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert client.head(f"/presence/api/media/{h}").status_code == 200
    # 再传一次：不重复保存
    r = put_media(client, PNG)
    assert (r.status_code, r.json()["created"]) == (200, False)


def test_media_upload_rejects_bad_input(env):
    client, _, settings = env
    assert put_media(client, PNG, token="nope").status_code == 401
    assert put_media(client, PNG, media_id="abc").status_code == 400
    assert put_media(client, PNG, media_id=hashlib.sha256(JPEG).hexdigest()).status_code == 400  # 哈希不符
    assert put_media(client, b"GIF89a" + b"\x00" * 10).status_code == 415
    assert put_media(client, b"\xff\xd8\xff" + b"\x00" * settings.media_max_bytes).status_code == 413
    assert not settings.media_dir.exists() or not any(settings.media_dir.iterdir())
    assert client.get("/presence/api/media/" + "0" * 64).status_code == 404


def test_music_card_cover_url(env):
    client, clock, settings = env
    h = hashlib.sha256(JPEG).hexdigest()
    put_media(client, JPEG)
    post(client, [{"kind": "music", "ts": T0, "data": {
        "player": "apple_music", "title": "A", "artist": "x", "album": "y",
        "state": "playing", "position": 3, "duration": 200, "cover_hash": h}}])
    clock.t += 15
    card = client.get("/presence/api/now").json()["main"]["card"]
    assert card["cover"] == f"{settings.media_base}/{h}"
    assert (card["title"], card["state"], card["duration"]) == ("A", "playing", 200)


def wait_for(client, pred, tries=50):
    """视频信息在后台任务里查，轮询到条件满足为止。"""
    import time

    for _ in range(tries):
        now = client.get("/presence/api/now").json()
        if pred(now):
            return now
        time.sleep(0.02)
    raise AssertionError(now)


def test_video_card_metadata_and_cover(env):
    from presence.sources.video import VideoMeta

    client, clock, settings = env
    p = client.app.state.presence
    calls = []

    def fake_meta(site, vid):
        calls.append((site, vid))
        return VideoMeta(title="标题", owner="UP主", url=f"https://www.bilibili.com/video/{vid}",
                         cover_url="https://i0.hdslb.com/x.jpg@480w", parts=["第一P", "第二P"])

    p.fetch_video_meta = fake_meta
    p.fetch_cover = lambda url, limit: JPEG
    sig = {"kind": "video", "ts": T0, "data": {"site": "bilibili", "id": "BV1xx411c7mD", "page": 2,
                                                 "state": "playing", "position": 10, "duration": 100}}
    post(client, [sig])
    now = wait_for(client, lambda n: n["main"].get("card", {}).get("title"))
    card = now["main"]["card"]
    h = hashlib.sha256(JPEG).hexdigest()
    assert card["title"] == "标题 · P2 第二P" and card["owner"] == "UP主"
    assert card["url"] == "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    assert card["cover"] == f"{settings.media_base}/{h}"
    assert client.get(f"/presence/api/media/{h}").content == JPEG
    assert p.db.get_media(h)["origin_url"] == "https://i0.hdslb.com/x.jpg@480w"
    post(client, [{**sig, "ts": T0 + 5}])
    assert calls == [("bilibili", "BV1xx411c7mD")]  # 查到后走缓存


def test_video_meta_failure_retried_later(env):
    client, clock, _ = env
    p = client.app.state.presence
    calls = []
    p.fetch_video_meta = lambda site, vid: calls.append(vid)  # 返回 None：没查到
    sig = {"kind": "video", "data": {"site": "youtube", "id": "abcdefg", "state": "playing"}}
    post(client, [sig])
    wait_for(client, lambda n: p.db.get_video_meta("youtube", "abcdefg") is not None)
    post(client, [sig])
    assert calls == ["abcdefg"]  # 刚失败过，不立刻重查
    clock.t += 3601
    post(client, [sig])
    wait_for(client, lambda n: len(calls) == 2)
    card = client.get("/presence/api/now").json()["main"]["card"]
    assert "title" not in card and card["id"] == "abcdefg"  # 没查到时只有进度


def test_video_signal_replaces_domain_item_without_double_count(env):
    client, clock, _ = env
    p = client.app.state.presence
    p.fetch_video_meta = lambda site, vid: None
    post(client, [
        {"kind": "desktop", "ts": T0, "ttl": 3600, "data": {"bundle_id": "com.apple.Safari", "domain": "bilibili.com"}},
        {"kind": "video", "ts": T0 + 60, "ttl": 300, "data": {"site": "bilibili", "id": "BV1", "state": "playing"}},
    ])
    clock.t = T0 + 120
    now = client.get("/presence/api/now").json()
    items = [now["main"], *now["side"]]
    assert [i["category"] for i in items] == ["video"] and "card" in now["main"]  # 只剩视频卡片
    assert client.get("/presence/api/today").json()["totals"]["activity"] == {"video": 120}  # 重叠只算一次


def test_web_card_icon_and_public_domain(env):
    client, clock, settings = env
    p = client.app.state.presence
    calls = []
    p.fetch_icon = lambda domain, limit: calls.append(domain) or Icon(PNG, "image/png", "https://x/icon.png")
    post(client, [{"kind": "desktop", "ts": T0, "data": {"bundle_id": "com.apple.Safari", "domain": "www.zhihu.com",
                                                         "title": "问题 - 知乎"}}])
    now = wait_for(client, lambda n: n["main"].get("card", {}).get("cover"))
    h = hashlib.sha256(PNG).hexdigest()
    assert now["main"]["card"] == {"kind": "web", "title": "问题 - 知乎", "domain": "www.zhihu.com",
                                   "cover": f"{settings.media_base}/{h}"}
    post(client, [{"kind": "desktop", "ts": T0 + 5, "data": {"bundle_id": "com.apple.Safari", "domain": "www.zhihu.com"}}])
    assert calls == ["www.zhihu.com"]  # 图标按域名缓存
    post(client, [{"kind": "desktop", "ts": T0 + 30, "data": {"bundle_id": "com.apple.Safari", "hidden": True}}])
    clock.t = T0 + 30
    main = client.get("/presence/api/now").json()["main"]
    assert (main["label"], "card" in main) == ("网上冲浪", False)


def test_svg_icon_served_with_safety_headers(env):
    client, clock, settings = env
    p = client.app.state.presence
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z" fill="#fff"/></svg>'
    p.fetch_icon = lambda domain, limit: Icon(svg, "image/svg+xml", "https://github.githubassets.com/favicon-dark.svg")
    post(client, [{"kind": "desktop", "ts": T0, "data": {"bundle_id": "com.apple.Safari", "domain": "github.com"}}])
    now = wait_for(client, lambda n: n["main"].get("card", {}).get("cover"))
    r = client.get("/presence/api/media/" + now["main"]["card"]["cover"].rsplit("/", 1)[1])
    assert r.content == svg and r.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    assert put_media(client, svg).status_code == 415  # 设备不能上传 SVG


def test_music_history_from_mac_and_windows(env):
    client, clock, _ = env
    p = client.app.state.presence
    jay = {"player": "aimp", "title": "晴天", "artist": "周杰伦", "album": "叶惠美", "duration": 269}
    yellow = {"player": "apple_music", "title": "Yellow", "artist": "Coldplay"}

    def at(dt, data, token=TOKEN):
        clock.t = T0 + dt
        post(client, [{"kind": "music", "ts": clock.t, "ttl": 300, "data": data}], token=token)

    at(0, {**jay, "state": "playing"}, WIN_TOKEN)
    at(10, {**yellow, "state": "playing"})
    at(100, {**jay, "state": "paused"}, WIN_TOKEN)
    at(120, {**jay, "state": "playing"}, WIN_TOKEN)  # 暂停 20 秒后继续
    at(250, {**yellow, "state": "paused"})
    at(290, {**jay, "state": "paused"}, WIN_TOKEN)
    clock.t = T0 + 600
    p.update()

    hist = [dict(r) for r in p.db.media_history("music", T0, clock.t)]
    assert [(h["title"], h["artist"], h["player"], h["source"], h["start"], h["end"], h["seconds"]) for h in hist] == [
        ("Yellow", "Coldplay", "apple_music", "mac", T0 + 10, T0 + 250, 240),
        ("晴天", "周杰伦", "aimp", "windows", T0, T0 + 290, 270),  # 暂停的 20 秒不算
    ]
    assert hist[1]["album"] == "叶惠美"
    top = [dict(r) for r in p.db.media_totals("music", T0, clock.t)]
    assert [(t["key"], t["seconds"], t["plays"]) for t in top] == [("周杰伦 - 晴天", 270, 1), ("Coldplay - Yellow", 240, 1)]


def test_phone_app_history_switch_close_and_reopen(env):
    client, clock, _ = env
    db = client.app.state.presence.db

    def at(dt, app, event="open"):
        clock.t = T0 + dt
        assert post(client, [{"kind": "phone_app", "ttl": 1800,
                              "data": {"app": app, "event": event}}], PHONE_TOKEN).status_code == 200

    # Mac 的实际歌曲独立记录，手机音乐 App 的前台状态不是歌曲播放。
    post(client, [{"kind": "music", "ttl": 1800, "data": {
        "player": "apple_music", "title": "Yellow", "state": "playing"}}])
    at(0, "X")
    at(20, "X")  # 重复打开续期，不另开记录
    at(60, "Apple Music")
    at(65, "X", "close")  # 上个 App 的延迟关闭不影响当前 App
    ongoing = db.media_history("app", T0, clock.t)
    assert (ongoing[0]["title"], ongoing[0]["end"], ongoing[0]["seconds"]) == ("Apple Music", None, 5)
    at(120, "Apple Music", "close")
    at(125, "Apple Music", "close")  # 重复关闭不重复计时
    at(140, "X")
    at(180, "X", "close")
    hist = db.media_history("app", T0, clock.t)
    assert [(r["title"], r["source"], r["start"], r["end"], r["seconds"]) for r in hist] == [
        ("X", "iphone", T0 + 140, T0 + 180, 40),
        ("Apple Music", "iphone", T0 + 60, T0 + 120, 60),
        ("X", "iphone", T0, T0 + 60, 60),
    ]
    assert [(r["key"], r["source"], r["seconds"], r["plays"])
            for r in db.media_totals("app", T0, clock.t)] == [
        ("X", "iphone", 100, 2), ("Apple Music", "iphone", 60, 1)]
    assert [(r["title"], r["source"], r["seconds"])
            for r in db.media_history("music", T0, clock.t)] == [("Yellow", "mac", 180)]


@pytest.mark.parametrize("tick_before_reopen", [False, True])
@pytest.mark.parametrize("next_app", ["X", "Claude"])
def test_phone_history_timeout_is_capped_at_expiry(env, tick_before_reopen, next_app):
    client, clock, _ = env
    p = client.app.state.presence
    post(client, [{"kind": "phone_app", "ttl": 1800, "data": {"app": "X"}}], PHONE_TOKEN)
    clock.t += 1900
    if tick_before_reopen:
        p.update()
    post(client, [{"kind": "phone_app", "ttl": 1800, "data": {"app": next_app}}], PHONE_TOKEN)
    hist = p.db.media_history("app", T0, clock.t + 10)
    assert len(hist) == 2
    assert (hist[1]["end"], hist[1]["seconds"]) == (T0 + 1800, 1800)
    # 日界线查询只累计查询范围内的时长
    assert p.db.media_totals("app", T0 + 600, T0 + 1200)[0]["seconds"] == 600


def test_history_totals_keep_same_content_on_devices_separate(env):
    client, clock, _ = env
    for token in (TOKEN, PHONE_TOKEN):
        post(client, [{"kind": "music", "ttl": 300, "data": {
            "title": "Yellow", "state": "playing"}}], token)
    clock.t += 60
    db = client.app.state.presence.db
    rows = db.media_totals("music", T0, clock.t)
    assert {(r["key"], r["source"], r["seconds"]) for r in rows} == {
        ("Yellow", "mac", 60), ("Yellow", "iphone", 60)}


def test_video_history_fills_title_later(env):
    from presence.sources.video import VideoMeta

    client, clock, _ = env
    p = client.app.state.presence

    def fake_meta(site, vid):
        if site == "youtube":
            return None  # 查不到：记录里只有视频 ID
        return VideoMeta(title="标题", owner="UP主", url=f"https://www.bilibili.com/video/{vid}")

    p.fetch_video_meta = fake_meta
    p.fetch_cover = lambda url, limit: None

    def at(dt, data):
        clock.t = T0 + dt
        post(client, [{"kind": "video", "ts": clock.t, "ttl": 300, "data": {"position": 0, "duration": 600, **data}}])

    bv = {"site": "bilibili", "id": "BV1xx411c7mD"}
    at(0, {**bv, "page": 1, "state": "playing"})  # 这时还没查到标题
    wait_for(client, lambda n: n["main"].get("card", {}).get("title"))
    at(100, {**bv, "page": 1, "state": "paused"})
    at(200, {**bv, "page": 2, "state": "playing"})  # 查到之后再看的，写入时就带标题
    at(300, {**bv, "page": 2, "state": "paused"})
    at(400, {"site": "youtube", "id": "dQw4w9WgXcQ", "state": "playing"})
    at(450, {"site": "youtube", "id": "dQw4w9WgXcQ", "state": "paused"})
    clock.t = T0 + 600
    p.update()

    hist = [dict(r) for r in p.db.media_history("video", T0, clock.t)]
    assert [(h["key"], h["title"], h["artist"], h["player"], h["page"], h["seconds"]) for h in hist] == [
        ("youtube:dQw4w9WgXcQ", None, None, "youtube", None, 50),
        ("bilibili:BV1xx411c7mD", "标题", "UP主", "bilibili", 2, 100),
        ("bilibili:BV1xx411c7mD", "标题", "UP主", "bilibili", 1, 100),
    ]
    top = [dict(r) for r in p.db.media_totals("video", T0, clock.t)]
    assert [(t["key"], t["seconds"], t["plays"]) for t in top] == [("bilibili:BV1xx411c7mD", 200, 2),
                                                                     ("youtube:dQw4w9WgXcQ", 50, 1)]
