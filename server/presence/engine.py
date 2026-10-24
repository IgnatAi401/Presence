"""状态引擎：把各设备的信号合并成「主状态 + 伴随状态」，并按线记录时间片段。

引擎本身只在内存里工作，所有时间都由调用方传入（ts / now），方便用模拟信号序列测试。
数据库读写由 app 层负责：它调用 drain_intervals() / drain_manual() 取出变化后落盘。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .rules import REFERENCE, Classified, Rules

ANCHOR = "anchor"
ACTIVITY = "activity"


@dataclass
class Timing:
    debounce_sec: float = 90  # Mac 前台活动切换后，新活动持续这么久才算数
    away_sec: float = 600  # Mac 空闲超过这么久算“离开”
    hold_sec: float = 1200  # 正事保持期（摸鱼最长保留多久）
    context_sec: float = 600  # 资料类网页继承最近多久内的正事
    eat_min: float = 60  # 吃饭默认时长
    sleep_max_h: float = 14  # 睡觉最长时长
    wake_active_sec: float = 600  # Mac 连续活跃这么久视为已起床
    out_min: float = 180  # “出门”默认有效期
    custom_min: float = 120  # 自定义状态默认有效期


@dataclass
class Course:
    event_id: str
    title: str
    start: float
    end: float
    location: str | None = None


@dataclass
class Interval:
    """一条线上的一个时间片段，对应数据库 segments 表的一行。"""

    line: str
    category: str
    start: float
    end: float | None = None
    tier: int | None = None
    source: str | None = None
    detail: str | None = None  # 公开的补充信息（游戏名、自定义文字等）
    project: str | None = None
    domain: str | None = None
    course: str | None = None
    slacking: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Item:
    """页面上显示的一项（主状态或伴随状态）。"""

    type: str  # anchor / activity / idle
    category: str
    label: str
    since: float | None = None
    until: float | None = None
    tier: int | None = None
    rank: int | None = None
    detail: str | None = None
    slacking_since: float | None = None
    card: dict[str, Any] | None = None
    project: str | None = None  # 仅私有
    domain: str | None = None  # 仅私有

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "category": self.category, "label": self.label}
        for k in ("tier", "since", "until", "detail", "card"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.slacking_since is not None:
            out["slacking"] = {"since": self.slacking_since}
        return out

    def private(self) -> dict[str, Any]:
        out = self.public()
        if self.project:
            out["project"] = self.project
        if self.domain:
            out["domain"] = self.domain
        return out


@dataclass
class Snapshot:
    main: Item
    side: list[Item]
    updated_at: float

    def public(self) -> dict[str, Any]:
        return {
            "main": self.main.public(),
            "side": [i.public() for i in self.side],
            "updated_at": self.updated_at,
        }


@dataclass
class ManualState:
    state: str  # eat / sleep / out / custom
    start: float
    expires_at: float
    text: str | None = None
    tier: int | None = None
    ended_at: float | None = None
    interval: Interval | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


MANUAL_SLOT = {"eat": "anchor", "sleep": "anchor", "out": "out", "custom": "custom"}


def _fmt(template: str, **values: Any) -> str:
    return template.format_map(defaultdict(str, {k: v for k, v in values.items() if v is not None})).strip()


# ---------------------------------------------------------------- Mac 前台活动


@dataclass
class MacAct:
    category: str
    project: str | None = None
    domain: str | None = None
    ref: bool = False  # 资料类网页继承了正事（显示“查资料”）

    def same(self, other: MacAct | None) -> bool:
        if other is None or self.category != other.category:
            return False
        return self.project is None or other.project is None or self.project == other.project


class MacTracker:
    """Mac 同一时刻只有一个前台活动；负责防抖、上下文继承、正事保持期和空闲判断。"""

    def __init__(self, engine: Engine):
        self.e = engine
        self.source: str | None = None
        self.last_seen: float | None = None
        self.expires: float | None = None
        self.idle = False
        self.active_since: float | None = None  # 本次连续活跃的开始时间（用于自动起床）

        self.committed: MacAct | None = None
        self.committed_since: float | None = None
        self.committed_iv: Interval | None = None
        self.pending: MacAct | None = None
        self.pending_since: float | None = None

        self.hold: MacAct | None = None  # 正在“摸鱼”的正事
        self.hold_since: float | None = None  # 这件正事最初开始的时间（显示用）
        self.slack_since: float | None = None
        self.slack_iv: Interval | None = None
        self.last_serious: tuple[MacAct, float] | None = None  # (正事, 结束时间)

    @property
    def rules(self) -> Rules:
        return self.e.rules

    @property
    def t(self) -> Timing:
        return self.e.timing

    def live(self, now: float) -> bool:
        return self.expires is not None and now <= self.expires

    # -- 输入

    def feed(self, source: str, ts: float, ttl: float, data: dict) -> None:
        if self.expires is not None and ts > self.expires:
            self.go_offline(self.last_seen or ts)
        self.check(ts)
        self.source = source
        self.last_seen = max(self.last_seen or ts, ts)
        self.expires = max(self.expires or 0, ts + ttl)

        idle_sec = float(data.get("idle_sec") or 0)
        if data.get("locked"):
            self.go_idle(ts)
            return
        if idle_sec >= self.t.away_sec:
            self.go_idle(ts - idle_sec)
            return
        if self.idle or self.active_since is None:
            self.active_since = ts - idle_sec
        self.idle = False

        c = self.rules.classify_desktop(data)
        if c is None:
            return
        self.propose(self.resolve(c, ts), ts)

    def heartbeat(self, ts: float, ttl: float) -> None:
        self.last_seen = max(self.last_seen or ts, ts)
        self.expires = max(self.expires or 0, ts + ttl)

    def resolve(self, c: Classified, ts: float) -> MacAct:
        if c.category != REFERENCE:
            return MacAct(c.category, c.project, c.domain)
        ctx = self.serious_context(ts)
        if ctx:
            return MacAct(ctx.category, ctx.project, c.domain, ref=True)
        return MacAct("browse", None, c.domain)

    def serious_context(self, ts: float) -> MacAct | None:
        if self.committed and self.rules.is_serious(self.committed.category):
            return self.committed
        if self.hold:
            return self.hold
        if self.last_serious and ts - self.last_serious[1] <= self.t.context_sec:
            return self.last_serious[0]
        return None

    def propose(self, act: MacAct, ts: float) -> None:
        if self.committed is None:
            self.pending = None
            self.commit(act, ts)
            return
        if act.same(self.committed):
            self.pending = None
            self.refresh(act)
            return
        if self.pending is not None and act.same(self.pending):
            self.pending = act
        else:
            self.pending, self.pending_since = act, ts
        self.check(ts)

    def refresh(self, act: MacAct) -> None:
        """同一活动内的细节变化（例如切到资料网页、补上项目名），不新开片段。"""
        cur = self.committed
        assert cur is not None
        cur.ref, cur.domain = act.ref, act.domain
        if cur.project is None and act.project:
            cur.project = act.project
            if self.committed_iv:
                self.committed_iv.project = act.project
                self.e.touch(self.committed_iv)

    # -- 时间推进

    def check(self, now: float) -> None:
        if self.expires is not None and now > self.expires:
            self.go_offline(self.last_seen or now)
            return
        commit_at = None
        if self.pending is not None and self.pending_since is not None and now - self.pending_since >= self.t.debounce_sec:
            commit_at = self.pending_since
        self.expire_hold(now, before=commit_at)
        if commit_at is not None and self.pending is not None:
            act, self.pending = self.pending, None
            self.commit(act, commit_at)
        self.expire_hold(now)

    def expire_hold(self, now: float, before: float | None = None) -> None:
        if self.hold is None or self.slack_since is None:
            return
        deadline = self.slack_since + self.t.hold_sec
        if deadline <= now and (before is None or deadline <= before):
            self.end_hold(deadline)

    def end_hold(self, at: float) -> None:
        if self.hold is not None and self.slack_since is not None:
            # 摸鱼期间实际在做的不是正事，上下文从开始摸鱼时算
            self.last_serious = (self.hold, self.slack_since)
        self.e.close(self.slack_iv, at)
        self.hold = self.hold_since = self.slack_since = self.slack_iv = None

    def commit(self, act: MacAct, at: float) -> None:
        old, old_since = self.committed, self.committed_since
        self.e.close(self.committed_iv, at)
        serious = self.rules.is_serious(act.category)

        resumed_since = None
        if self.hold is not None and act.same(self.hold):
            # 保持期内回到正事：摸鱼标记消失，记一次“被打断”
            resumed_since = self.hold_since
            if act.project is None:
                act.project = self.hold.project
            self.e.close(self.slack_iv, at)
            self.hold = self.hold_since = self.slack_since = self.slack_iv = None
        elif self.hold is not None and serious:
            self.end_hold(at)

        if old is not None and self.rules.is_serious(old.category):
            if not serious:
                self.hold, self.hold_since, self.slack_since = old, old_since, at
                self.slack_iv = self.e.open(
                    ACTIVITY, old.category, at, source=self.source, project=old.project, slacking=True
                )
            else:
                self.last_serious = (old, at)

        self.committed = act
        self.committed_since = resumed_since if resumed_since is not None else at
        self.committed_iv = self.e.open(
            ACTIVITY,
            act.category,
            at,
            source=self.source,
            project=act.project,
            domain=act.domain,
            meta={"resumed": True} if resumed_since is not None else {},
        )

    def go_idle(self, at: float) -> None:
        self._stop(at)
        self.idle = True

    def go_offline(self, at: float) -> None:
        self._stop(at)
        self.idle = False
        self.expires = None

    def _stop(self, at: float) -> None:
        """离开电脑：不适用保持期，所有 Mac 活动在空闲开始时结束。"""
        if self.committed_iv is not None:
            at = max(at, self.committed_iv.start)
        if self.committed is not None and self.rules.is_serious(self.committed.category):
            self.last_serious = (self.committed, at)
        if self.hold is not None and self.slack_since is not None:
            self.last_serious = (self.hold, self.slack_since)
        self.e.close(self.committed_iv, at)
        self.e.close(self.slack_iv, at)
        self.committed = self.committed_since = self.committed_iv = None
        self.pending = self.pending_since = None
        self.hold = self.hold_since = self.slack_since = self.slack_iv = None
        self.active_since = None

    # -- 显示

    def items(self) -> list[Item]:
        out = []
        if self.committed is not None:
            a = self.committed
            out.append(self.e.activity_item(
                a.category, self.committed_since, detail="查资料" if a.ref else None,
                project=a.project, domain=a.domain,
                values={"name": self.rules.names.get(a.domain or "")},
            ))
        if self.hold is not None:
            out.append(self.e.activity_item(
                self.hold.category, self.hold_since, slacking_since=self.slack_since, project=self.hold.project
            ))
        return out


# ---------------------------------------------------------------- 信号类活动


@dataclass
class Feed:
    """音乐、视频、游戏、手机 App 这类“开始 / 结束”型活动。"""

    key: str
    category: str
    identity: tuple
    source: str
    data: dict
    since: float
    last_ts: float
    expires: float
    interval: Interval


def _feed_spec(kind: str, data: dict) -> tuple[str, bool, tuple] | None:
    """返回 (类别, 是否进行中, 身份)；身份变了就开新片段（例如换歌）。"""
    if kind == "music":
        return "music", data.get("state") == "playing", (data.get("title"), data.get("artist"))
    if kind == "video":
        return "video", data.get("state") == "playing", (data.get("site"), data.get("id"), data.get("page"))
    if kind == "game":
        return "game", data.get("event", "start") != "stop", (data.get("exe") or data.get("name"),)
    if kind == "phone_app":
        return "phone", data.get("event", "open") != "close", (data.get("app"),)
    return None


# ---------------------------------------------------------------- 引擎


class Engine:
    def __init__(self, rules: Rules, timing: Timing | None = None, started_at: float = 0):
        self.rules = rules
        self.timing = timing or Timing()
        self.started_at = started_at  # 服务器启动时间；恢复的状态不早于它开新片段
        self.mac = MacTracker(self)
        self.manual: dict[str, ManualState] = {}
        self.feeds: dict[str, Feed] = {}
        self.courses: list[Course] = []
        self.cancelled: set[str] = set()
        self.class_ivs: dict[str, Interval] = {}
        self.source_expiry: dict[str, float] = {}
        self._dirty: dict[str, Interval] = {}
        self._manual_dirty: dict[str, ManualState] = {}

    # -- 片段记录

    def open(self, line: str, category: str, start: float, **kw: Any) -> Interval:
        tier = self.rules.tier(category) if line == ACTIVITY else None
        iv = Interval(line=line, category=category, start=start, tier=tier, **kw)
        self.touch(iv)
        return iv

    def close(self, iv: Interval | None, end: float) -> None:
        if iv is not None and iv.end is None:
            iv.end = max(end, iv.start)
            self.touch(iv)

    def touch(self, iv: Interval) -> None:
        self._dirty[iv.id] = iv

    def drain_intervals(self) -> list[Interval]:
        out, self._dirty = list(self._dirty.values()), {}
        return out

    def drain_manual(self) -> list[ManualState]:
        out, self._manual_dirty = list(self._manual_dirty.values()), {}
        return out

    # -- 输入

    def ingest(self, source: str, kind: str, ts: float, ttl: float, data: dict | None = None) -> None:
        data = data or {}
        self.source_expiry[source] = max(self.source_expiry.get(source, 0), ts + ttl)
        if kind == "desktop":
            self.mac.feed(source, ts, ttl, data)
        elif kind == "heartbeat":
            if self.mac.source == source:
                self.mac.heartbeat(ts, ttl)
            for f in self.feeds.values():
                if f.source == source:
                    f.last_ts, f.expires = max(f.last_ts, ts), max(f.expires, ts + ttl)
        elif kind == "manual":
            self._manual(source, ts, data)
        else:
            self._feed(source, kind, ts, ttl, data)

    def _feed(self, source: str, kind: str, ts: float, ttl: float, data: dict) -> None:
        spec = _feed_spec(kind, data)
        if spec is None:
            return
        category, active, identity = spec
        key = f"{kind}:{source}"
        if kind in ("game", "phone_app"):
            key += f":{identity[0]}"
        if kind == "phone_app" and active:
            # 手机同一时刻只有一个前台 App
            for k, f in list(self.feeds.items()):
                if f.source == source and f.category == "phone" and f.identity != identity:
                    self._end_feed(k, ts)

        cur = self.feeds.get(key)
        if cur and (not active or cur.identity != identity or ts > cur.expires):
            self._end_feed(key, ts if ts <= cur.expires else cur.last_ts)
            cur = None
        if not active:
            return
        if cur:
            cur.data, cur.last_ts, cur.expires = data, ts, ts + ttl
            return
        detail = data.get("name") if category == "game" else None
        iv = self.open(ACTIVITY, category, ts, source=source, detail=detail)
        self.feeds[key] = Feed(key, category, identity, source, data, ts, ts, ts + ttl, iv)

    def _end_feed(self, key: str, at: float) -> None:
        f = self.feeds.pop(key, None)
        if f:
            self.close(f.interval, at)

    def _manual(self, source: str, ts: float, data: dict) -> None:
        state = data.get("state")
        t = self.timing
        if state == "clear":
            for m in list(self.manual.values()):
                self.end_manual(m, ts)
        elif state == "wake":
            m = self.manual.get("anchor")
            if m and m.state == "sleep":
                self.end_manual(m, ts)
        elif state == "cancel_class":
            for c in self.courses:
                if c.start <= ts < c.end:
                    self.cancelled.add(c.event_id)
            self._sync_courses(ts)
        elif state in MANUAL_SLOT:
            minutes = data.get("minutes")
            if state == "sleep":
                limit = t.sleep_max_h * 60
                minutes = min(float(minutes), limit) if minutes else limit
            else:
                default = {"eat": t.eat_min, "out": t.out_min, "custom": t.custom_min}[state]
                minutes = float(minutes) if minutes else default
            tier = int(data["tier"]) if state == "custom" and data.get("tier") else None
            m = ManualState(state, ts, ts + minutes * 60, text=data.get("text") or None, tier=tier)
            self.start_manual(m, ts, source)

    def start_manual(self, m: ManualState, at: float, source: str | None = None) -> None:
        slot = MANUAL_SLOT[m.state]
        old = self.manual.get(slot)
        if old:
            self.end_manual(old, at)
        line = ANCHOR if slot == "anchor" else ACTIVITY
        category = m.state
        m.interval = self.open(line, category, max(at, self.started_at), source=source, detail=m.text)
        if m.tier is not None:
            m.interval.tier = m.tier
        self.manual[slot] = m
        self._manual_dirty[m.id] = m

    def restore_manual(self, m: ManualState) -> None:
        """服务器重启后恢复仍有效的手动状态；新片段从启动时开始。"""
        self.start_manual(m, self.started_at, None)

    def end_manual(self, m: ManualState, at: float) -> None:
        m.ended_at = at
        if m.interval:
            self.close(m.interval, at)
        slot = MANUAL_SLOT[m.state]
        if self.manual.get(slot) is m:
            del self.manual[slot]
        self._manual_dirty[m.id] = m

    def set_courses(self, courses: list[Course], now: float) -> None:
        self.courses = sorted(courses, key=lambda c: c.start)
        self._sync_courses(now)

    # -- 时间推进

    def tick(self, now: float) -> None:
        self.mac.check(now)
        for key, f in list(self.feeds.items()):
            if now > f.expires:
                self._end_feed(key, f.last_ts)
        for m in list(self.manual.values()):
            if now >= m.expires_at:
                self.end_manual(m, m.expires_at)
        sleep = self.manual.get("anchor")
        a = self.mac.active_since
        if sleep and sleep.state == "sleep" and a is not None and a >= sleep.start and now - a >= self.timing.wake_active_sec:
            self.end_manual(sleep, a)
        self._sync_courses(now)

    def _sync_courses(self, now: float) -> None:
        current = {c.event_id: c for c in self.courses if c.start <= now < c.end and c.event_id not in self.cancelled}
        for eid, iv in list(self.class_ivs.items()):
            if eid not in current:
                course = next((c for c in self.courses if c.event_id == eid), None)
                self.close(iv, min(now, course.end) if course else now)
                del self.class_ivs[eid]
        for eid, c in current.items():
            if eid not in self.class_ivs:
                self.class_ivs[eid] = self.open(
                    ANCHOR, "class", max(c.start, self.started_at), course=c.title, meta={"event_id": eid}
                )

    # -- 显示

    def activity_item(self, category: str, since: float | None, values: dict | None = None, **kw: Any) -> Item:
        d = self.rules.activities.get(category)
        label = _fmt(d.label, **(values or {})) if d else category
        if category == "video" and values and values.get("name"):
            label = f"在看 {values['name']}"
        return Item(type=ACTIVITY, category=category, label=label or category, since=since,
                    tier=d.tier if d else 9, **kw)

    def _anchor_item(self, category: str, since: float, until: float | None, **values: Any) -> Item:
        d = self.rules.anchors[category]
        return Item(type=ANCHOR, category=category, label=_fmt(d.label, **values), since=since, until=until, rank=d.rank)

    def snapshot(self, now: float) -> Snapshot:
        anchors: list[Item] = []
        for eid, iv in self.class_ivs.items():
            c = next(c for c in self.courses if c.event_id == eid)
            anchors.append(self._anchor_item("class", c.start, c.end, course=c.title))
        if m := self.manual.get("anchor"):
            anchors.append(self._anchor_item(m.state, m.start, None))

        acts: list[Item] = self.mac.items()
        for slot in ("out", "custom"):
            if m := self.manual.get(slot):
                it = self.activity_item(m.state, m.start, values={"text": m.text})
                if m.state == "out" and m.text:
                    it.label = f"出门：{m.text}"
                if m.tier is not None:
                    it.tier = m.tier
                acts.append(it)

        music = [f for f in self.feeds.values() if f.category == "music"]
        latest_music = max(music, key=lambda f: f.last_ts, default=None)
        for f in self.feeds.values():
            if f.category == "music" and f is not latest_music:
                continue  # 多台设备同时放歌时只显示最近更新的一个
            acts.append(self._feed_item(f))

        anchors.sort(key=lambda i: (i.rank, -(i.since or 0)))
        acts.sort(key=lambda i: (i.tier, -(i.since or 0)))
        if anchors:
            main, side = anchors[0], anchors[1:] + acts
        elif acts:
            main, side = acts[0], acts[1:]
        else:
            live = any(exp >= now for exp in self.source_expiry.values())
            main = Item(type="idle", category="away" if live else "offline", label="暂时离开" if live else "离线")
            side = []
        return Snapshot(main=main, side=side, updated_at=now)

    def _feed_item(self, f: Feed) -> Item:
        d = f.data
        if f.category == "music":
            card = {k: d.get(k) for k in ("title", "artist", "album", "state", "position", "duration")}
            card["cover_hash"] = d.get("cover_hash")
            card["updated_at"] = f.last_ts
            return self.activity_item("music", f.since, values={"title": d.get("title")}, card=card)
        if f.category == "video":
            card = {k: d.get(k) for k in ("site", "id", "page", "state", "position", "duration")}
            card["updated_at"] = f.last_ts
            name = {"bilibili": "B站", "youtube": "YouTube"}.get(d.get("site") or "")
            return self.activity_item("video", f.since, values={"name": name}, card=card)
        if f.category == "game":
            card = {"name": d.get("name"), "appid": d.get("appid"), "since": f.since}
            return self.activity_item("game", f.since, values={"name": d.get("name") or d.get("exe")}, card=card)
        return self.activity_item(f.category, f.since, values={"app": d.get("app")})
