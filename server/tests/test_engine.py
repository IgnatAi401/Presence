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
    assert s.main.category == "research" and s.main.detail == "查资料"
    assert len(sim.segments("research")) == 1


def test_reference_during_hold_resumes_serious():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    sim.mac(900, safari("github.com"))
    s = sim.snap(1000)
    assert cats(s) == ("research", []) and s.main.detail == "查资料" and s.main.slacking_since is None


def test_reference_after_hold_expired_is_browse():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    assert sim.snap(1801).main.category == "video"
    sim.mac(1900, safari("github.com"))
    assert sim.snap(2000).main.category == "browse"


# ---------------------------------------------------------------- 防抖


def test_short_switch_is_ignored():
    sim = Sim().mac(0, VSCODE).mac(100, safari("www.bilibili.com")).mac(160, VSCODE)
    s = sim.snap(400)
    assert cats(s) == ("dev", [])
    assert [iv.category for iv in sim.segments()] == ["dev"]


def test_switch_commits_after_debounce_backdated():
    sim = Sim().mac(0, safari("www.youtube.com")).mac(100, safari("example.com"))
    assert sim.snap(150).main.category == "video"
    s = sim.snap(200)
    assert s.main.category == "browse" and s.main.since == T0 + 100
    video, browse = sim.segments("video")[0], sim.segments("browse")[0]
    assert video.end == T0 + 100 and browse.start == T0 + 100


# ---------------------------------------------------------------- 摸鱼与保持期


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


def test_slacking_expires_after_hold():
    sim = Sim().mac(0, MATLAB).mac(600, safari("www.bilibili.com"))
    for t in range(720, 2400, 120):
        sim.mac(t, safari("www.bilibili.com"))
    s = sim.snap(600 + 1200 + 1)
    assert cats(s) == ("video", [])
    slack = sim.segments("research", slacking=True)[0]
    assert slack.end == T0 + 1800


def test_idle_ends_serious_without_hold():
    sim = Sim().mac(0, MATLAB).mac(1200, MATLAB, idle=650)
    s = sim.snap(1210)
    assert (s.main.category) == "away"
    assert sim.segments("research")[0].end == T0 + 550


def test_switch_to_other_serious_ends_hold():
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
    assert cats(sim.snap(210)) == ("dev", [])


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
    assert sim.snap(110).main.category == "away"


@pytest.mark.parametrize("tier_order", [("research", "game", "video", "music")])
def test_side_sorted_by_tier(tier_order):
    sim = Sim()
    e = sim.e
    e.ingest("windows", "music", T0, 300, {"title": "A", "state": "playing"})
    e.ingest("windows", "game", T0, 300, {"name": "Hades", "event": "start"})
    e.ingest("mac", "video", T0, 300, {"site": "bilibili", "id": "BV1", "state": "playing"})
    sim.mac(0, MATLAB)
    assert cats(sim.snap(10)) == (tier_order[0], list(tier_order[1:]))
