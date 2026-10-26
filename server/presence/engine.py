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
from .signals import FEED_CATEGORY

ANCHOR = "anchor"
ACTIVITY = "activity"


@dataclass
class Timing:
    away_sec: float = 600  # Mac 空闲超过这么久算“离开”
    enter_sec: float = 10  # Mac 切到新活动后停留这么久才显示；更短的切换当作没发生
    context_sec: float = 600  # 资料类网页继承最近多久内的正事
    eat_min: float = 60  # 吃饭默认时长
    sleep_max_h: float = 14  # 睡觉最长时长
    wake_active_sec: float = 600  # Mac 连续活跃这么久视为已起床
    out_min: float = 1440  # 出去玩手动结束，24 小时兜底
    custom_min: float = 120  # 自定义状态默认有效期
    office_max_h: float = 12  # “在办公室”最长时长（忘了结束时兜底）


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
    activity: str | None = None  # 主状态合并了哪个活动（显示它的今日时长）
    project: str | None = None  # 仅私有
    domain: str | None = None  # 仅私有
    activity_name: str | None = None  # 合并进来的活动的简称（“今日科研”）
    app: str | None = None  # 手机前台 App 名
    icon: str | None = None  # 公开页本地图标：/icons/apps/<icon>.png

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "category": self.category, "label": self.label}
        for k in ("tier", "since", "until", "detail", "card", "activity", "activity_name", "app", "icon"):
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


MANUAL_SLOT = {"eat": "anchor", "sleep": "anchor", "office": "office", "out": "out", "custom": "custom"}
ANCHOR_SLOTS = ("anchor", "office")
# 开始一个手动状态时顺带结束的其他槽位：到办公室就不算出门，出门或睡觉就离开了办公室
ENDS_SLOTS = {"office": ("out",), "out": ("office",), "sleep": ("office", "out")}


def public_host(host: str | None) -> bool:
    """公网域名才显示网页卡片：localhost、局域网名字、IP 地址都不算。"""
    if not host or "." not in host or host.endswith((".local", ".localhost", ".lan")):
        return False
    return not host.replace(".", "").isdigit() and ":" not in host


def _fmt(template: str, **values: Any) -> str:
    return template.format_map(defaultdict(str, {k: v for k, v in values.items() if v is not None})).strip()


# ---------------------------------------------------------------- Mac 前台活动


@dataclass
class MacAct:
    category: str
    project: str | None = None
    domain: str | None = None  # 命中的规则域名（bilibili.com），用来取显示名
    host: str | None = None  # Safari 当前网页的实际域名（www.bilibili.com），网页卡片用
    title: str | None = None  # Safari 当前网页的标题

    def same(self, other: MacAct | None) -> bool:
        if other is None or self.category != other.category:
            return False
        return self.project is None or other.project is None or self.project == other.project


@dataclass
class Linger:
    """离开后仍在页面上显示的活动（余温）。正事的余温就是“摸鱼中”。"""

    act: MacAct
    since: float  # 这件事最初开始的时间（显示用）
    left_at: float
    until: float
    slack_iv: Interval | None = None


class MacTracker:
    """Mac 同一时刻只有一个前台活动；负责余温、上下文继承和空闲判断。

    切到新活动停留 enter_sec 后显示（从切过去的那一刻算起）；旧活动按等级保留一段时间
    （rules.yaml 的 keep_min），期间回来就接着算。切到同一等级的活动时，旧活动立刻消失。
    """

    def __init__(self, engine: Engine):
        self.e = engine
        self.source: str | None = None
        self.last_seen: float | None = None
        self.expires: float | None = None
        self.idle = False
        self.active_since: float | None = None  # 本次连续活跃的开始时间（用于自动起床）

        self.current: MacAct | None = None
        self.current_since: float | None = None
        self.current_iv: Interval | None = None
        self.lingers: dict[int, Linger] = {}  # 按等级存放：同一等级互斥，最多一个
        self.pending: MacAct | None = None  # 刚切过去、还没停够 enter_sec 的活动
        self.pending_since: float | None = None
        self.last_serious: tuple[MacAct, float] | None = None  # (正事, 结束时间)
        self.page: tuple[str, str | None] | None = None  # Safari 此刻在前台显示的网页 (域名, 标题)

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

        host = (data.get("domain") or "").lower()
        self.page = (host, data.get("title") or None) if public_host(host) and not data.get("private") else None

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
        act = self.resolve(c, ts)
        if c.domain:
            act.host = (data.get("domain") or "").lower() or None
            act.title = data.get("title") or None
        self.propose(act, ts)

    def heartbeat(self, ts: float, ttl: float) -> None:
        self.last_seen = max(self.last_seen or ts, ts)
        self.expires = max(self.expires or 0, ts + ttl)

    def resolve(self, c: Classified, ts: float) -> MacAct:
        if c.category != REFERENCE:
            return MacAct(c.category, c.project, c.domain)
        ctx = self.serious_context(ts)
        if ctx:
            return MacAct(ctx.category, ctx.project, c.domain)
        return MacAct("browse", None, c.domain)

    def serious_context(self, ts: float) -> MacAct | None:
        if self.current and self.rules.is_serious(self.current.category):
            return self.current
        for lg in self.lingers.values():
            if self.rules.is_serious(lg.act.category):
                return lg.act
        if self.last_serious and ts - self.last_serious[1] <= self.t.context_sec:
            return self.last_serious[0]
        return None

    def propose(self, act: MacAct, ts: float) -> None:
        if self.current is None:
            self.pending = self.pending_since = None
            self.enter(act, ts)  # 原来什么都没有时直接显示
        elif act.same(self.current):
            self.pending = self.pending_since = None
            self.refresh(act)
        elif self.pending is not None and act.same(self.pending):
            self.pending = act
            self.check(ts)
        else:
            self.pending, self.pending_since = act, ts
            self.check(ts)

    def enter(self, act: MacAct, ts: float) -> None:
        tier = self.rules.tier(act.category)
        old, old_since = self.current, self.current_since
        self.e.close(self.current_iv, ts)

        resumed = None
        for t, lg in list(self.lingers.items()):
            if act.same(lg.act):
                resumed = self.lingers.pop(t)
                self.e.close(resumed.slack_iv, ts)
            elif t == tier:
                self.end_linger(t, ts)

        if old is not None and old_since is not None:
            if self.rules.tier(old.category) != tier:
                self.start_linger(old, old_since, ts)
            elif self.rules.is_serious(old.category):
                self.last_serious = (old, ts)

        interrupted = False
        if resumed is not None:
            if act.project is None:
                act.project = resumed.act.project
            interrupted = ts - resumed.left_at >= self.t.enter_sec
        self.current = act
        self.current_since = resumed.since if resumed is not None else ts
        self.current_iv = self.e.open(
            ACTIVITY,
            act.category,
            ts,
            source=self.source,
            project=act.project,
            domain=act.domain,
            meta={"resumed": True} if interrupted else {},
        )

    def refresh(self, act: MacAct) -> None:
        """同一活动内的细节变化（例如切到资料网页、换了网页、补上项目名），不新开片段。"""
        cur = self.current
        assert cur is not None
        cur.domain, cur.host, cur.title = act.domain, act.host, act.title
        if cur.project is None and act.project:
            cur.project = act.project
            if self.current_iv:
                self.current_iv.project = act.project
                self.e.touch(self.current_iv)

    # -- 余温

    def start_linger(self, act: MacAct, since: float, at: float) -> None:
        keep = self.rules.keep_sec(act.category)
        tier = self.rules.tier(act.category)
        self.end_linger(tier, at)
        serious = self.rules.is_serious(act.category)
        if keep <= 0:
            if serious:
                self.last_serious = (act, at)
            return
        # 正事的余温单独记一段“摸鱼”片段：时间线上仍显示正事，但不计入正事时长
        slack_iv = self.e.open(ACTIVITY, act.category, at, source=self.source, project=act.project,
                               slacking=True) if serious else None
        self.lingers[tier] = Linger(act, since, at, at + keep, slack_iv)

    def end_linger(self, tier: int, at: float) -> None:
        lg = self.lingers.pop(tier, None)
        if lg is None:
            return
        if self.rules.is_serious(lg.act.category):
            # 摸鱼期间实际在做的不是正事，上下文从离开时算
            self.last_serious = (lg.act, lg.left_at)
        self.e.close(lg.slack_iv, at)

    # -- 时间推进

    def check(self, now: float) -> None:
        if self.expires is not None and now > self.expires:
            self.go_offline(self.last_seen or now)
            return
        if self.pending is not None and self.pending_since is not None \
                and now - self.pending_since >= self.t.enter_sec:
            # 余温按切过去的时间算，先把在那之前到期的结束掉
            self.expire_lingers(self.pending_since)
            act, at = self.pending, self.pending_since
            self.pending = self.pending_since = None
            self.enter(act, at)
        self.expire_lingers(now)

    def expire_lingers(self, now: float) -> None:
        for t, lg in list(self.lingers.items()):
            if lg.until <= now:
                self.end_linger(t, lg.until)

    def go_idle(self, at: float) -> None:
        self._stop(at)
        self.idle = True

    def go_offline(self, at: float) -> None:
        self._stop(at)
        self.idle = False
        self.expires = None

    def _stop(self, at: float) -> None:
        """离开电脑：不留余温，所有 Mac 活动在空闲开始时结束。"""
        self.page = None
        if self.current_iv is not None:
            at = max(at, self.current_iv.start)
        for t in list(self.lingers):
            self.end_linger(t, at)
        if self.current is not None and self.rules.is_serious(self.current.category):
            self.last_serious = (self.current, at)
        self.e.close(self.current_iv, at)
        self.current = self.current_since = self.current_iv = None
        self.pending = self.pending_since = None
        self.active_since = None

    # -- 显示

    def items(self) -> list[Item]:
        out = []
        cur_tier = 99
        if self.current is not None:
            a = self.current
            cur_tier = self.rules.tier(a.category)
            # Safari 在前台、且显示的就是这个活动的网页时附上网页卡片（标题、域名、图标）。
            # 刚切到别的网页、还没停够 enter_sec，或切到了没有网址的页面 / 别的 App 时不附，免得显示旧网页
            card = None
            if self.page and a.host == self.page[0]:
                card = {"kind": "web", "title": self.page[1], "domain": self.page[0]}
            out.append(self.e.activity_item(
                a.category, self.current_since, card=card,
                project=a.project, domain=a.domain,
                values={"name": self.rules.site_names.get(a.domain or "")},
            ))
        for lg in self.lingers.values():
            a = lg.act
            slacking = self.rules.is_serious(a.category) and self.rules.tier(a.category) < cur_tier
            out.append(self.e.activity_item(
                a.category, lg.since, slacking_since=lg.left_at if slacking else None,
                project=a.project, domain=a.domain,
                values={"name": self.rules.site_names.get(a.domain or "")},
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
    until: float | None = None  # 主动结束后的余温截止时间
    session: float | None = None  # 连续进行的起点：换歌、余温内恢复都接着算（since 只是这一首 / 这一段）
    data_ts: float | None = None  # data（进度等）对应的时间；心跳只续期，不改它


def _feed_spec(kind: str, data: dict) -> tuple[str, bool, tuple] | None:
    """返回 (类别, 是否进行中, 身份)；身份变了就开新片段（例如换歌）。"""
    category = FEED_CATEGORY.get(kind)
    if category == "music":
        return category, data.get("state") == "playing", (data.get("title"), data.get("artist"))
    if category == "video":
        # 暂停着（看完了）但在看评论区，也算还在看
        active = data.get("state") == "playing" or data.get("viewing") == "comments"
        return category, active, (data.get("site"), data.get("id"), data.get("page"))
    if category == "game":
        return category, data.get("event", "start") != "stop", (data.get("exe") or data.get("name"),)
    if category == "phone":
        return category, data.get("event", "open") != "close", (data.get("app"),)
    return None


def _media_meta(category: str, data: dict, identity: tuple) -> dict[str, Any] | None:
    """历史明细：key 区分歌曲、游戏、视频或手机 App，设备来源另存。"""
    if category == "phone":
        app = data.get("app")
        if isinstance(app, str) and app:
            return {"type": "app", "key": app, "title": app}
        return None
    if category == "game":
        name = data.get("name") or data.get("exe")
        return {"type": "game", "key": str(data.get("appid") or identity[0]), "title": name}
    if category == "music":
        title, artist = data.get("title") or "", data.get("artist") or ""
        if not title:
            return None
        return {"type": "music", "key": f"{artist} - {title}" if artist else title, "title": title,
                "artist": artist or None, "album": data.get("album") or None, "player": data.get("player")}
    if category == "video":
        site, vid, page = identity
        if not (isinstance(site, str) and isinstance(vid, str)):
            return None
        # 标题和 UP 主由服务器查到后再补（app 层），引擎只知道是哪个视频
        return {"type": "video", "key": f"{site}:{vid}", "site": site, "vid": vid,
                "page": int(page) if isinstance(page, (int, float)) else None}
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
        self.ended: dict[str, Feed] = {}  # 已结束、仍在余温里显示的
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
        elif kind == "music" and data.get("player"):
            key += f":{data['player']}"  # 同一台设备上的两个播放器也各算各的，不互相顶掉
        if kind == "phone_app" and active:
            # 手机同一时刻只有一个前台 App
            for k, f in list(self.feeds.items()):
                if f.source == source and f.category == "phone" and f.identity != identity:
                    self._end_feed(k, min(ts, f.expires))

        cur = self.feeds.get(key)
        session = None
        privacy_changed = cur is not None and bool(cur.data.get("private")) != bool(data.get("private"))
        if cur and (not active or cur.identity != identity or ts > cur.expires or privacy_changed):
            if active and ts <= cur.expires:
                session = cur.session  # 直接换歌
            if ts > cur.expires:
                self._end_feed(key, cur.expires if category == "phone" else cur.last_ts)
            else:
                # 主动停止（暂停、关闭）才留余温；换歌、换 App 直接替换
                stopped = not active and cur.identity == identity and not privacy_changed
                cur.data = {**cur.data, **data}
                cur.last_ts = cur.data_ts = ts  # 卡片的 updated_at 对应暂停那一刻的进度
                self._end_feed(key, ts, linger=stopped and category != "phone")
            cur = None
        if not active:
            prev = self.ended.get(key)
            if prev is not None and bool(prev.data.get("private")) != bool(data.get("private")):
                self.ended.pop(key)
            return
        if cur:
            cur.data, cur.last_ts, cur.data_ts, cur.expires = data, ts, ts, ts + ttl
            return
        prev = self.ended.pop(key, None)
        if prev is not None and ts < (prev.until or 0):
            session = session or prev.session  # 暂停后在余温内继续（同一首或下一首）
        if prev is not None and (prev.identity != identity or ts >= (prev.until or 0)
                                 or bool(prev.data.get("private")) != bool(data.get("private"))):
            prev = None
        tier = self.rules.tier(category)
        for k, f in list(self.ended.items()):
            if f.source == source and self.rules.tier(f.category) == tier:
                del self.ended[k]  # 同一等级互斥
        detail = data.get("name") if category == "game" else None
        interrupted = prev is not None and ts - (prev.interval.end or ts) >= self.timing.enter_sec
        meta: dict[str, Any] = {"resumed": True} if interrupted else {}
        if data.get("private"):
            meta["private"] = True
        iv = self.open(ACTIVITY, category, ts, source=source, detail=detail, meta=meta)
        if media := _media_meta(category, data, identity):
            # 暂停后在余温内继续同一首（同一个游戏、同一个视频），算同一次播放
            media["play"] = prev.interval.meta.get("media", {}).get("play") if prev is not None else None
            media["play"] = media["play"] or iv.id
            meta["media"] = media
        since = prev.since if prev is not None else ts
        self.feeds[key] = Feed(key, category, identity, source, data, since, ts, ts + ttl, iv, session=session or since,
                               data_ts=ts)

    def _end_feed(self, key: str, at: float, linger: bool = False) -> None:
        f = self.feeds.pop(key, None)
        if not f:
            return
        self.close(f.interval, at)
        keep = self.rules.keep_sec(f.category)
        if linger and keep > 0:
            f.until = at + keep
            self.ended[key] = f

    def _manual(self, source: str, ts: float, data: dict) -> None:
        """data 已由 signals.normalize 翻译成 {state, action}；action 为 stop 且没有 state 时结束全部。"""
        state = data.get("state")
        t = self.timing
        if data.get("action") == "stop":
            if state is None:
                for m in list(self.manual.values()):
                    self.end_manual(m, ts)
            elif state == "class":
                for c in self.courses:
                    if c.start <= ts < c.end:
                        self.cancelled.add(c.event_id)
                self._sync_courses(ts)
            elif state in MANUAL_SLOT:
                m = self.manual.get(MANUAL_SLOT[state])
                if m and m.state == state:  # 吃饭和睡觉共用一个槽位，只结束说到的那个
                    self.end_manual(m, min(ts, m.expires_at))
        elif state in MANUAL_SLOT:
            minutes = data.get("minutes")
            if state in ("sleep", "office"):
                limit = (t.sleep_max_h if state == "sleep" else t.office_max_h) * 60
                minutes = min(float(minutes), limit) if minutes else limit
            else:
                default = {"eat": t.eat_min, "out": t.out_min, "custom": t.custom_min}[state]
                minutes = float(minutes) if minutes else default
            if state == "out":
                minutes = min(max(minutes, 1), 1440)
            tier = int(data["tier"]) if state == "custom" and data.get("tier") else None
            text = data.get("text") or None
            if state == "out" and isinstance(text, str):
                text = text.strip() or None
            m = ManualState(state, ts, ts + minutes * 60, text=text, tier=tier)
            self.start_manual(m, ts, source)

    def start_manual(self, m: ManualState, at: float, source: str | None = None) -> None:
        slot = MANUAL_SLOT[m.state]
        for s in (slot, *ENDS_SLOTS.get(m.state, ())):
            if old := self.manual.get(s):
                self.end_manual(old, min(at, old.expires_at))
        line = ANCHOR if slot in ANCHOR_SLOTS or (m.state == "custom" and m.tier == 1) else ACTIVITY
        category = m.state
        # 出游由明确的开始/结束信号界定，停机期间仍在出游；固定 ID 让重启恢复同一片段。
        start = m.start if m.state == "out" else max(at, self.started_at)
        identity = {"id": m.id} if m.state == "out" else {}
        m.interval = self.open(line, category, start, source=source, detail=m.text, **identity)
        if m.tier is not None:
            m.interval.tier = m.tier
        self.manual[slot] = m
        self._manual_dirty[m.id] = m

    def restore_manual(self, m: ManualState) -> None:
        """恢复有效手动状态；出游连续计时，其他状态从启动时开新片段。"""
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
                self._end_feed(key, f.expires if f.category == "phone" else f.last_ts)
        for key, f in list(self.ended.items()):
            if f.until is None or now >= f.until:
                del self.ended[key]
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
            label = f"看 {values['name']}"  # 主状态前面有“正在”，这里不再写“在”
        if category == "phone" and values and (app := self.rules.phone_apps.get(values.get("app") or "")):
            label, kw["icon"] = app.label, app.icon
        if d and d.show_project and kw.get("project") and not kw.get("detail"):
            kw["detail"] = kw["project"]  # 搞科研 / 搞开发时公开工作区文件夹名
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
        for slot in ANCHOR_SLOTS:
            if m := self.manual.get(slot):
                anchors.append(self._anchor_item(m.state, m.start, None))

        feeds = [f for f in (*self.feeds.values(), *self.ended.values()) if not f.data.get("private")]
        acts: list[Item] = self.mac.items()
        site_since = None  # 被视频卡片取代的“在看 B站”从什么时候开始
        if any(f.category == "video" and f.source == self.mac.source for f in feeds):
            # 同一台 Mac 上报了视频详情：按域名判断的“在看 B站”和视频卡片是同一件事，只留卡片
            site_since = min((i.since for i in acts if i.category == "video" and i.since), default=None)
            acts = [i for i in acts if i.category != "video"]
        for slot in ("out", "custom"):
            if m := self.manual.get(slot):
                if m.state == "custom" and m.tier == 1:
                    anchors.append(self._anchor_item("custom", m.start, None, text=m.text))
                    continue
                it = self.activity_item(m.state, m.start, values={"text": m.text})
                if m.state == "out" and m.text:
                    it.label = f"出去玩 · {m.text}"
                if m.tier is not None:
                    it.tier = m.tier
                acts.append(it)

        for f in feeds:  # 多台设备同时放歌时各显示一张卡片（Apple Music 和 AIMP 并存）
            it = self._feed_item(f)
            if f.category == "video" and f.source == self.mac.source and site_since and it.card:
                # 连续看视频从打开网站算起，换成卡片显示后不从零开始
                it.card["session_since"] = min(it.card["session_since"], site_since)
            acts.append(it)

        anchors.sort(key=lambda i: (i.rank, -(i.since or 0)))
        acts.sort(key=lambda i: (i.tier, -(i.since or 0)))
        if anchors:
            main, side = anchors[0], anchors[1:] + acts
            self._fold(main, side)
        elif acts:
            main, side = acts[0], acts[1:]
        else:
            live = any(exp >= now for exp in self.source_expiry.values())
            main = Item(type="idle", category="away" if live else "offline", label="暂时离开" if live else "离线")
            side = []
        return Snapshot(main=main, side=side, updated_at=now)

    def _fold(self, main: Item, side: list[Item]) -> None:
        """锚定状态自带的活动（例如办公室的科研）正在进行时，合并进主状态，不再单列。"""
        d = self.rules.anchors.get(main.category)
        if not d or not d.activity:
            return
        it = next((i for i in side if i.type == ACTIVITY and i.category == d.activity), None)
        if it is None:
            return
        side.remove(it)
        main.activity = d.activity
        main.activity_name = self.rules.short(d.activity)
        main.slacking_since = it.slacking_since
        main.project = it.project
        main.detail = it.detail

    def _feed_item(self, f: Feed) -> Item:
        d = f.data
        if f.category == "music":
            card = {k: d.get(k) for k in ("player", "title", "artist", "album", "state", "position", "duration")}
            card["cover_hash"] = d.get("cover_hash")
            card["player_name"] = self.rules.players.get(d.get("player") or "")
            card["updated_at"] = f.data_ts
            card["session_since"] = f.session  # 连续听歌的起点
            return self.activity_item("music", f.since, values={"title": d.get("title")}, card=card)
        if f.category == "video":
            card = {k: d.get(k) for k in ("site", "id", "page", "state", "position", "duration", "rate", "viewing")}
            card["updated_at"] = f.data_ts
            card["session_since"] = f.session  # 连续看视频的起点：换视频、余温内继续都接着算
            name = self.rules.video_sites.get(d.get("site") or "")
            card["site_name"] = name
            # 在看评论区时标题不变，只在卡片小字里注明（card.viewing）
            return self.activity_item("video", f.since, values={"name": name}, card=card)
        if f.category == "game":
            card = {"name": d.get("name"), "appid": d.get("appid"), "since": f.since}
            return self.activity_item("game", f.since, values={"name": d.get("name") or d.get("exe")}, card=card)
        return self.activity_item(f.category, f.since, values={"app": d.get("app")}, app=d.get("app"))
