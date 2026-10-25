"""用模拟信号序列测试状态引擎（时间单位：秒，从 T0 开始）。"""

from pathlib import Path

import pytest

from presence.engine import Course, Engine
from presence.rules import Rules

RULES = Rules.load(Path(__file__).resolve().parent.parent / "config" / "rules.yaml")
T0 = 1_800_000_000.0
MATLAB = {"bundle_id": "com.mathworks.matlab", "app": "MATLAB"}
VSCODE = {"bundle_id": "com.microsoft.VSCode", "app": "Visual Studio Code"}
FINDER = {"bundle_id": "com.apple.finder", "app": "Finder"}


def safari(domain):
    return {"bundle_id": "com.apple.Safari", "app": "Safari", "domain": domain}


class Sim:
    def __init__(self):
        self.e = Engine(RULES)
        self.ivs = {}

    def at(self, t):
        return T0 + t

    def mac(self, t, data, idle=0, ttl=3600):
        self.e.ingest("mac", "desktop", self.at(t), ttl, {**data, "idle_sec": idle})
        return self

    def manual(self, t, state, **kw):
        self.e.ingest("iphone", "manual", self.at(t), 60, {"state": state, **kw})
        return self

    def tick(self, t):
        self.e.tick(self.at(t))
        return self

    def snap(self, t):
        self.tick(t)
        s = self.e.snapshot(self.at(t))
        self.collect()
        return s

    def collect(self):
        for iv in self.e.drain_intervals():
            self.ivs[iv.id] = iv

    def segments(self, category=None, slacking=None):
        self.collect()
        out = [iv for iv in self.ivs.values() if category in (None, iv.category)]
        if slacking is not None:
            out = [iv for iv in out if iv.slacking == slacking]
        return sorted(out, key=lambda iv: iv.start)


def cats(snap):
    return snap.main.category, [i.category for i in snap.side]


# ---------------------------------------------------------------- 基本分类


def test_offline_when_nothing():
    s = Sim().snap(0)
    assert (s.main.type, s.main.category) == ("idle", "offline")


def test_first_activity_shows_immediately():
    s = Sim().mac(0, MATLAB).snap(5)
    assert s.main.category == "research" and s.main.label == "搞科研"


def test_project_overrides_app_default():
    s = Sim().mac(0, {**VSCODE, "project": "thesis-sim"}).snap(1)
    assert s.main.category == "research" and s.main.project == "thesis-sim"
    s = Sim().mac(0, {**VSCODE, "project": "unknown-proj"}).snap(1)
    assert s.main.category == "dev"


def test_project_shown_publicly_for_research_and_dev():
    s = Sim().mac(0, {**VSCODE, "project": "unknown-proj"}).snap(1)
    assert s.public()["main"]["detail"] == "unknown-proj" and "project" not in s.public()["main"]
    s = Sim().manual(0, "office").mac(0, {**VSCODE, "project": "thesis-sim"}).snap(1)
    assert (s.main.category, s.main.detail) == ("office", "thesis-sim")
    assert "detail" not in Sim().mac(0, VSCODE).snap(1).public()["main"]


def test_unknown_app_keeps_current():
    sim = Sim().mac(0, MATLAB).mac(30, FINDER)
    assert sim.snap(500).main.category == "research"


def test_overleaf_is_research_and_bilibili_video():
    assert Sim().mac(0, safari("www.overleaf.com")).snap(1).main.category == "research"
    s = Sim().mac(0, safari("www.bilibili.com")).snap(1)
    assert s.main.category == "video" and s.main.label == "在看 B站"


def test_reference_without_context_is_browse():
    assert Sim().mac(0, safari("arxiv.org")).snap(1).main.category == "browse"


def test_reference_inherits_research():
    sim = Sim().mac(0, MATLAB)
    for t in range(60, 1200, 120):  # MATLAB 和 arXiv 来回切换
        sim.mac(t, safari("arxiv.org")).mac(t + 60, MATLAB)
    sim.mac(1300, safari("arxiv.org"))
    s = sim.snap(1500)
    assert s.main.category == "research" and s.main.detail is None  # 不再写“查资料”，改附网页卡片
    assert s.main.card == {"kind": "web", "title": None, "domain": "arxiv.org"}
    assert len(sim.segments("research")) == 1


def test_reference_during_linger_resumes_serious():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    sim.mac(900, safari("github.com"))
    s = sim.snap(1000)
    assert cats(s) == ("research", []) and s.main.card["domain"] == "github.com" and s.main.slacking_since is None


def test_reference_after_linger_expired_is_browse():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    assert sim.snap(1801).main.category == "video"
    sim.mac(1900, safari("github.com"))
    assert sim.snap(2000).main.category == "browse"


# ---------------------------------------------------------------- 进入与余温


def test_switch_shows_after_enter_delay():
    sim = Sim().mac(0, VSCODE).mac(100, safari("www.bilibili.com"))
    assert cats(sim.snap(105)) == ("dev", [])
    s = sim.snap(110)
    assert cats(s) == ("dev", ["video"]) and s.main.slacking_since == T0 + 100
    assert s.side[0].since == T0 + 100  # 从切过去的那一刻算起
    assert sim.segments("dev")[0].end == T0 + 100


def test_same_tier_replaces_immediately():
    sim = Sim().mac(0, MATLAB).mac(100, VSCODE)
    assert cats(sim.snap(110)) == ("dev", [])
    sim = Sim().mac(0, safari("www.youtube.com")).mac(100, safari("example.com"))
    assert cats(sim.snap(110)) == ("browse", [])
    assert sim.segments("video")[0].end == T0 + 100


def test_low_tier_lingers_one_minute():
    sim = Sim().mac(0, safari("www.bilibili.com")).mac(100, MATLAB)
    s = sim.snap(130)
    assert cats(s) == ("research", ["video"]) and s.side[0].label == "在看 B站"
    assert s.main.slacking_since is None
    assert cats(sim.snap(161)) == ("research", [])
    assert sim.segments("video")[0].end == T0 + 100  # 余温不计入时长


def test_return_within_linger_continues():
    sim = Sim().mac(0, safari("www.bilibili.com")).mac(100, VSCODE).mac(130, safari("www.bilibili.com"))
    s = sim.snap(140)
    assert s.main.category == "dev" and s.main.slacking_since == T0 + 130
    assert s.side[0].category == "video" and s.side[0].since == T0


def test_glance_is_ignored():
    # 瞄一眼 B站 5 秒就回来：页面不闪，统计里也不出现
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    assert cats(sim.snap(604)) == ("research", [])
    sim.mac(605, MATLAB)
    s = sim.snap(700)
    assert cats(s) == ("research", []) and s.main.since == T0
    assert [iv.category for iv in sim.segments()] == ["research"]


def test_alternating_serious_drops_old_video():
    # 关掉 B站后在 MATLAB 和 VS Code 之间来回切：B站一分钟后消失
    sim = Sim().mac(0, safari("www.bilibili.com"))
    for i, t in enumerate(range(100, 200, 10)):
        sim.mac(t, MATLAB if i % 2 == 0 else VSCODE)
    s = sim.snap(195)
    assert s.main.category == "research" and s.side == []  # 停够 10 秒的最后一个是 180 秒的 MATLAB


# ---------------------------------------------------------------- 摸鱼与正事余温


def test_slacking_then_return():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    s = sim.snap(700)
    assert s.main.category == "research" and s.main.slacking_since == T0 + 600
    assert s.main.since == T0
    assert [i.category for i in s.side] == ["video"]

    sim.mac(1000, MATLAB)
    s = sim.snap(1100)
    assert cats(s) == ("research", [])
    assert s.main.slacking_since is None and s.main.since == T0

    research = sim.segments("research", slacking=False)
    slack = sim.segments("research", slacking=True)
    assert [(iv.start - T0, iv.end and iv.end - T0) for iv in research] == [(0, 600), (1000, None)]
    assert [(iv.start - T0, iv.end - T0) for iv in slack] == [(600, 1000)]
    assert research[1].meta == {"resumed": True}


def test_slacking_expires_after_linger():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    for t in range(720, 2400, 120):
        sim.mac(t, safari("www.bilibili.com"))
    s = sim.snap(600 + 600 + 1)
    assert cats(s) == ("video", [])
    slack = sim.segments("research", slacking=True)[0]
    assert slack.end == T0 + 1200


def test_idle_ends_serious_without_linger():
    sim = Sim().mac(0, MATLAB).mac(1200, MATLAB, idle=650)
    s = sim.snap(1210)
    assert (s.main.category) == "away"
    assert sim.segments("research")[0].end == T0 + 550


def test_switch_to_other_serious_ends_linger():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    sim.snap(700)
    sim.mac(800, VSCODE)
    s = sim.snap(900)
    assert cats(s) == ("dev", [])
    assert sim.segments("research", slacking=True)[0].end == T0 + 800


# ---------------------------------------------------------------- 过期与离线


def test_ttl_expiry_goes_offline_at_last_seen():
    sim = Sim().mac(0, MATLAB, ttl=300)
    e = sim.e
    e.ingest("mac", "heartbeat", T0 + 120, 300)
    assert sim.snap(400).main.category == "research"
    s = sim.snap(421)
    assert s.main.category == "offline"
    assert sim.segments("research")[0].end == T0 + 120


def test_away_while_other_device_alive():
    sim = Sim().mac(0, MATLAB, idle=900, ttl=600)
    assert sim.snap(10).main.category == "away"


# ---------------------------------------------------------------- 锚定状态


def course(start, end, title="信号与系统", eid="c1"):
    return Course(eid, title, T0 + start, T0 + end)


def test_class_is_main_and_activity_side():
    sim = Sim()
    sim.e.set_courses([course(100, 5000)], T0)
    sim.mac(200, MATLAB, ttl=6000)
    s = sim.snap(300)
    assert s.main.category == "class" and s.main.label == "正在上《信号与系统》"
    assert [i.category for i in s.side] == ["research"]
    s = sim.snap(5001)
    assert s.main.category == "research"
    assert sim.segments("class")[0].end == T0 + 5000


def test_eat_during_class_is_first_side_then_promoted():
    sim = Sim()
    sim.e.set_courses([course(0, 3000)], T0)
    sim.mac(10, MATLAB).manual(100, "eat")
    s = sim.snap(200)
    assert cats(s) == ("class", ["eat", "research"])
    s = sim.snap(3001)
    assert cats(s) == ("eat", ["research"])


def test_eat_expires_and_sleep_replaces_eat():
    sim = Sim().manual(0, "eat")
    assert sim.snap(10).main.category == "eat"
    assert sim.snap(3601).main.category == "offline"
    sim.manual(4000, "eat").manual(4100, "sleep")
    assert cats(sim.snap(4200)) == ("sleep", [])
    eats = sim.segments("eat")
    assert eats[1].end == T0 + 4100


def test_out_is_main_without_anchor_but_side_in_class():
    sim = Sim().manual(0, "out", text="出去玩")
    s = sim.snap(10)
    assert s.main.category == "out" and s.main.label == "出门：出去玩"
    sim.e.set_courses([course(0, 3000)], T0 + 20)
    assert cats(sim.snap(30)) == ("class", ["out"])


def test_cancel_class():
    sim = Sim()
    sim.e.set_courses([course(0, 3000)], T0)
    sim.manual(600, "cancel_class")
    assert sim.snap(700).main.category == "offline"
    assert sim.segments("class")[0].end == T0 + 600


def test_clear_ends_manual_states():
    sim = Sim().manual(0, "sleep").manual(10, "custom", text="写作业", tier=1)
    s = sim.snap(20)
    assert s.main.category == "sleep" and s.side[0].label == "写作业" and s.side[0].tier == 1
    sim.manual(30, "clear")
    assert sim.snap(40).main.category != "sleep"
    assert not sim.e.manual


def test_mac_active_ten_minutes_ends_sleep():
    sim = Sim().manual(0, "sleep")
    sim.mac(3600, MATLAB)
    for t in range(3720, 4300, 120):
        sim.mac(t, MATLAB)
    assert sim.snap(4100).main.category == "sleep"
    assert sim.snap(4201).main.category == "research"
    assert sim.segments("sleep")[0].end == T0 + 3600


def test_wake_ends_sleep():
    sim = Sim().manual(0, "sleep").manual(100, "wake")
    assert sim.snap(110).main.category == "away"  # 快捷指令的信号本身还没过期
    assert sim.segments("sleep")[0].end == T0 + 100


# ---------------------------------------------------------------- 办公室


def test_office_folds_research_and_lists_dev():
    sim = Sim().manual(0, "office").mac(10, MATLAB, ttl=6000)
    s = sim.snap(20)
    assert cats(s) == ("office", []) and s.main.label == "在办公室搞科研"
    assert s.main.activity == "research" and s.main.since == T0
    sim.mac(100, VSCODE)
    assert cats(sim.snap(110)) == ("office", ["dev"])


def test_office_shows_slacking():
    sim = Sim().manual(0, "office").mac(10, MATLAB, ttl=6000).mac(600, safari("www.bilibili.com"))
    s = sim.snap(610)
    assert cats(s) == ("office", ["video"]) and s.main.slacking_since == T0 + 600
    assert s.public()["main"]["slacking"] == {"since": T0 + 600}


def test_office_survives_idle_and_ranks_below_eat():
    sim = Sim().manual(0, "office").mac(10, MATLAB, ttl=6000).mac(1200, MATLAB, idle=900, ttl=6000)
    assert cats(sim.snap(1210)) == ("office", [])
    sim.manual(1300, "eat")
    assert cats(sim.snap(1310)) == ("eat", ["office"])
    sim.manual(1400, "clear")
    assert sim.snap(1410).main.category == "away"


def test_leave_office_and_max_duration():
    sim = Sim().manual(0, "office").manual(100, "leave_office")
    assert sim.snap(110).main.category != "office"
    assert sim.segments("office")[0].end == T0 + 100
    sim = Sim().manual(0, "office")
    assert sim.snap(12 * 3600 - 1).main.category == "office"
    assert sim.snap(12 * 3600 + 1).main.category != "office"


def test_office_and_out_end_each_other_sleep_ends_office():
    sim = Sim().manual(0, "out", text="出去玩").manual(100, "office")
    assert cats(sim.snap(110)) == ("office", [])
    sim.manual(200, "out")
    assert sim.snap(210).main.category == "out"
    sim.manual(300, "office").manual(400, "sleep")
    assert cats(sim.snap(410)) == ("sleep", [])


def test_class_outranks_office():
    sim = Sim().manual(0, "office")
    sim.e.set_courses([course(100, 3000)], T0 + 50)
    assert cats(sim.snap(200)) == ("class", ["office"])


# ---------------------------------------------------------------- 信号类活动


def test_music_feed_and_track_change():
    sim = Sim()
    e = sim.e
    e.ingest("windows", "music", T0, 300, {"title": "A", "artist": "x", "state": "playing", "position": 3})
    sim.mac(10, VSCODE)
    s = sim.snap(20)
    assert cats(s) == ("dev", ["music"]) and s.side[0].label == "正在听《A》"
    e.ingest("windows", "music", T0 + 100, 300, {"title": "B", "artist": "x", "state": "playing"})
    assert len(sim.segments("music")) == 2
    e.ingest("windows", "music", T0 + 200, 300, {"title": "B", "artist": "x", "state": "paused"})
    s = sim.snap(210)
    assert cats(s) == ("dev", ["music"]) and s.side[0].card["state"] == "paused"
    assert cats(sim.snap(261)) == ("dev", [])
    assert sim.segments("music")[1].end == T0 + 200


def test_music_resume_within_linger_keeps_since():
    e = (sim := Sim()).e
    e.ingest("windows", "music", T0, 300, {"title": "A", "state": "playing"})
    e.ingest("windows", "music", T0 + 100, 300, {"title": "A", "state": "paused"})
    e.ingest("windows", "music", T0 + 130, 300, {"title": "A", "state": "playing"})
    s = sim.snap(140)
    assert s.main.since == T0 and s.main.card["state"] == "playing"
    assert sim.segments("music")[1].meta["resumed"] is True


def test_music_from_two_players_coexist():
    e = (sim := Sim()).e
    e.ingest("mac", "music", T0, 300, {"player": "apple_music", "title": "A", "state": "playing"})
    e.ingest("windows", "music", T0 + 10, 300, {"player": "aimp", "title": "B", "state": "playing"})
    s = sim.snap(20)
    cards = [s.main.card, *(i.card for i in s.side)]
    assert sorted(c["player"] for c in cards) == ["aimp", "apple_music"]
    e.ingest("windows", "music", T0 + 30, 300, {"player": "aimp", "title": "B", "state": "paused"})
    s = sim.snap(40)
    assert len(s.side) == 1 and {s.main.card["player"], s.side[0].card["player"]} == {"aimp", "apple_music"}


def test_two_players_on_one_device_do_not_replace_each_other():
    e = (sim := Sim()).e
    for i in range(3):  # 两个播放器交替上报
        e.ingest("mac", "music", T0 + i * 20, 300, {"player": "apple_music", "title": "A", "state": "playing"})
        e.ingest("mac", "music", T0 + i * 20 + 10, 300, {"player": "aimp", "title": "B", "state": "playing"})
    s = sim.snap(70)
    assert sorted(c["player"] for c in [s.main.card, *(i.card for i in s.side)]) == ["aimp", "apple_music"]
    assert len(sim.segments("music")) == 2  # 没有因为来回切换而切出一堆片段


def test_game_lingers_three_minutes():
    e = (sim := Sim()).e
    e.ingest("windows", "game", T0, 3600, {"name": "Hades", "event": "start"})
    e.ingest("windows", "game", T0 + 600, 3600, {"name": "Hades", "event": "stop"})
    assert sim.snap(779).main.category == "game"
    assert sim.snap(781).main.category == "away"


def test_only_music_is_main():
    sim = Sim()
    sim.e.ingest("windows", "music", T0, 300, {"title": "A", "state": "playing"})
    assert sim.snap(10).main.category == "music"


def test_phone_app_switch_and_close():
    sim = Sim()
    e = sim.e
    e.ingest("iphone", "phone_app", T0, 1800, {"app": "X", "event": "open"})
    e.ingest("iphone", "phone_app", T0 + 60, 1800, {"app": "Instagram", "event": "open"})
    s = sim.snap(70)
    assert s.main.label == "在刷 Instagram" and s.side == []
    e.ingest("iphone", "phone_app", T0 + 100, 1800, {"app": "Instagram", "event": "close"})
    assert sim.snap(110).main.label == "在刷 Instagram"
    assert sim.snap(161).main.category == "away"


@pytest.mark.parametrize("tier_order", [("research", "game", "video", "music")])
def test_side_sorted_by_tier(tier_order):
    sim = Sim()
    e = sim.e
    e.ingest("windows", "music", T0, 300, {"title": "A", "state": "playing"})
    e.ingest("windows", "game", T0, 300, {"name": "Hades", "event": "start"})
    e.ingest("mac", "video", T0, 300, {"site": "bilibili", "id": "BV1", "state": "playing"})
    sim.mac(0, MATLAB)
    assert cats(sim.snap(10)) == (tier_order[0], list(tier_order[1:]))


def test_music_session_spans_track_changes_and_short_pauses():
    e = (sim := Sim()).e
    e.ingest("windows", "music", T0, 300, {"title": "A", "state": "playing"})
    e.ingest("windows", "music", T0 + 200, 300, {"title": "B", "state": "playing"})  # 换歌
    card = sim.snap(210).main.card
    assert (card["session_since"], sim.snap(210).main.since) == (T0, T0 + 200)
    e.ingest("windows", "music", T0 + 300, 300, {"title": "B", "state": "paused"})
    e.ingest("windows", "music", T0 + 330, 300, {"title": "C", "state": "playing"})  # 余温内换下一首
    assert sim.snap(340).main.card["session_since"] == T0
    e.ingest("windows", "music", T0 + 400, 300, {"title": "C", "state": "paused"})
    e.ingest("windows", "music", T0 + 500, 300, {"title": "C", "state": "playing"})  # 停了超过 1 分钟
    assert sim.snap(510).main.card["session_since"] == T0 + 500


def test_music_pause_updates_card_time():
    e = (sim := Sim()).e
    e.ingest("windows", "music", T0, 300, {"title": "A", "state": "playing", "position": 0})
    e.ingest("windows", "music", T0 + 90, 300, {"title": "A", "state": "paused", "position": 90})
    card = sim.snap(100).main.card
    assert (card["state"], card["position"], card["updated_at"], card["session_since"]) == ("paused", 90, T0 + 90, T0)


def test_video_feed_hides_domain_video_from_same_mac():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    sim.e.ingest("mac", "video", T0 + 620, 300, {"site": "bilibili", "id": "BV1", "state": "playing"})
    s = sim.snap(630)
    assert cats(s) == ("research", ["video"]) and s.side[0].card["id"] == "BV1"
    assert s.main.slacking_since == T0 + 600  # 摸鱼标记不受影响
    sim.e.ingest("windows", "video", T0 + 640, 300, {"site": "youtube", "id": "x", "state": "playing"})
    s = sim.snap(650)
    assert sorted(i.card["id"] for i in s.side) == ["BV1", "x"]  # 别的设备的视频照常显示


def test_domain_video_shown_without_video_feed():
    s = Sim().mac(0, safari("www.bilibili.com")).snap(5)
    assert cats(s) == ("video", []) and s.main.card["kind"] == "web"


def test_video_session_spans_videos_and_starts_at_site():
    sim = Sim().mac(0, safari("www.bilibili.com"))
    sim.e.ingest("mac", "video", T0 + 100, 300, {"site": "bilibili", "id": "BV1", "state": "playing"})
    s = sim.snap(110)
    assert s.main.card["session_since"] == T0  # 从打开 B站 算起
    sim.e.ingest("mac", "video", T0 + 200, 300, {"site": "bilibili", "id": "BV2", "state": "playing"})  # 换视频
    s = sim.snap(210)
    assert (s.main.since, s.main.card["session_since"]) == (T0 + 200, T0)
    sim.e.ingest("windows", "video", T0 + 220, 300, {"site": "youtube", "id": "x", "state": "playing"})
    yt = next(i for i in sim.snap(230).side + [sim.snap(230).main] if i.card["id"] == "x")
    assert yt.card["session_since"] == T0 + 220  # 别的设备不沾 Mac 打开网站的时间


def test_heartbeat_does_not_move_card_progress_time():
    e = (sim := Sim()).e
    e.ingest("mac", "video", T0, 300, {"site": "bilibili", "id": "BV1", "state": "playing", "position": 10})
    e.ingest("mac", "heartbeat", T0 + 60, 300)
    card = sim.snap(70).main.card
    assert (card["position"], card["updated_at"]) == (10, T0)  # 网页按 T0 推算进度，不会往回跳


def test_web_card_follows_current_page_and_hidden_is_browse():
    sim = Sim().mac(0, {**safari("www.zhihu.com"), "title": "问题 A - 知乎"})
    s = sim.snap(5)
    assert (s.main.category, s.main.card) == ("browse", {"kind": "web", "title": "问题 A - 知乎", "domain": "www.zhihu.com"})
    sim.mac(20, {**safari("sspai.com"), "title": "文章"})  # 同类别换网页：立即更新，不新开片段
    assert sim.snap(21).main.card["domain"] == "sspai.com" and len(sim.segments("browse")) == 1
    sim.mac(40, {"bundle_id": "com.apple.Safari", "app": "Safari", "hidden": True})
    s = sim.snap(41)
    assert (s.main.category, s.main.label, s.main.card) == ("browse", "网上冲浪", None)


def test_hidden_site_does_not_inherit_research():
    sim = Sim().mac(0, MATLAB).mac(100, {"bundle_id": "com.apple.Safari", "app": "Safari", "hidden": True})
    s = sim.snap(200)
    assert s.main.category == "research" and s.main.slacking_since == T0 + 100
    assert [(i.category, i.card) for i in s.side] == [("browse", None)]


def test_web_card_only_on_current_activity():
    sim = Sim().mac(0, {**safari("www.zhihu.com"), "title": "知乎"}).mac(100, MATLAB)
    s = sim.snap(120)
    assert cats(s) == ("research", ["browse"]) and s.side[0].card is None  # 离开 Safari 后不再显示网页


def test_web_card_never_shows_stale_page():
    sim = Sim().mac(0, VSCODE).mac(100, {**safari("github.com"), "title": "GitHub"})
    assert sim.snap(101).main.card["title"] == "GitHub"  # 资料网页并入开发，立即显示
    sim.mac(110, {"bundle_id": "com.apple.Safari", "app": "Safari"})  # Safari 起始页，没有网址
    assert sim.snap(111).main.card is None
    sim.mac(120, {**safari("www.bilibili.com"), "title": "B站视频"})  # 切到别的类别，还没停够 10 秒
    s = sim.snap(125)
    assert (s.main.category, s.main.card) == ("dev", None)
    s = sim.snap(131)  # 开发进入余温（摸鱼），视频卡片在伴随状态
    assert (s.main.category, s.main.card, s.side[0].card["title"]) == ("dev", None, "B站视频")
    sim.mac(140, FINDER)  # 切到不分类的 App：沿用看视频，但不再显示网页
    assert sim.snap(141).side[0].card is None


def test_no_web_card_for_local_hosts():
    for host in ("localhost", "127.0.0.1", "nas.local", "192.168.1.5"):
        s = Sim().mac(0, VSCODE).mac(10, {**safari(host), "title": "本地"}).snap(11)
        assert s.main.card is None, host


def test_music_media_meta_shares_play_across_pause():
    e = (sim := Sim()).e
    song = {"player": "aimp", "title": "晴天", "artist": "周杰伦", "album": "叶惠美"}
    e.ingest("windows", "music", T0, 300, {**song, "state": "playing"})
    e.ingest("windows", "music", T0 + 100, 300, {**song, "state": "paused"})
    e.ingest("windows", "music", T0 + 130, 300, {**song, "state": "playing"})  # 余温 1 分钟内继续
    e.ingest("windows", "music", T0 + 200, 300, {**song, "title": "七里香", "album": "七里香", "state": "playing"})
    a, b, c = (iv.meta["media"] for iv in sim.segments("music"))
    assert a == {"type": "music", "key": "周杰伦 - 晴天", "title": "晴天", "artist": "周杰伦", "album": "叶惠美",
                 "player": "aimp", "play": a["play"]}
    assert b["play"] == a["play"]  # 暂停后继续：同一次播放
    assert c["key"] == "周杰伦 - 七里香" and c["play"] != a["play"]  # 换歌：新的一次


def test_music_replay_after_linger_is_new_play():
    e = (sim := Sim()).e
    e.ingest("mac", "music", T0, 300, {"player": "apple_music", "title": "A", "state": "playing"})
    e.ingest("mac", "music", T0 + 100, 300, {"player": "apple_music", "title": "A", "state": "paused"})
    e.ingest("mac", "music", T0 + 400, 300, {"player": "apple_music", "title": "A", "state": "playing"})
    a, b = (iv.meta["media"] for iv in sim.segments("music"))
    assert a["key"] == "A" and a["artist"] is None and b["play"] != a["play"]


def test_music_without_title_not_logged():
    e = (sim := Sim()).e
    e.ingest("mac", "music", T0, 300, {"player": "apple_music", "title": "", "state": "playing"})
    assert "media" not in sim.segments("music")[0].meta
