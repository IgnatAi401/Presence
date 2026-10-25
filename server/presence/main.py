"""应用入口：信号接收、公开接口、SSE 推送和定时任务。

启动：uv run uvicorn presence.main:app --host 127.0.0.1 --port 8790
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from . import stats
from .config import CONFIG_DIR, Settings
from .db import DB
from .engine import Engine, Snapshot, public_host
from .rules import Rules
from .sources import steam, video, web
from .sources.calendar import CalendarUnavailable, fetch_courses

log = logging.getLogger("presence")

PREFIX = "/presence/api"
TICK_SEC = 2  # 状态推进间隔：决定切换防抖、余温到期多快反映到页面
SSE_PING_SEC = 25
SIGNAL_KEEP_DAYS = 30
MEDIA_ID = re.compile(r"[0-9a-f]{64}")
MEDIA_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/svg+xml": "svg"}
# 图片接口的安全响应头：SVG 里即使带了脚本，直接打开图片地址时也不会执行
MEDIA_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",  # 文件名就是内容的哈希，永远不会变
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}
VIDEO_META_RETRY_SEC = 3600  # 视频信息没查到时，过这么久再查
SITE_ICON_RETRY_SEC = 86400  # 网站图标没取到时，过这么久再取
DOMAIN = re.compile(r"[a-z0-9.-]{1,253}")
STEAM_RETRY_SEC = 300  # 游戏封面没取到时，过这么久再取


def sniff_image(data: bytes) -> str | None:
    """按文件头判断图片类型，只认 JPEG / PNG / WebP；不信任客户端的 Content-Type。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class SignalIn(BaseModel):
    kind: Literal["desktop", "video", "music", "manual", "phone_app", "game", "heartbeat"]
    ts: float | None = None
    ttl: float = Field(180, gt=0, le=86400)
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_outing(self):
        if self.kind == "manual" and self.data.get("state") == "custom" and "tier" in self.data:
            tier = self.data["tier"]
            if isinstance(tier, bool) or not isinstance(tier, (int, float)) or not 1 <= tier <= 2147483647 or tier % 1:
                raise ValueError("自定义优先级须为正整数：1 为锚定，2、3…为活动等级")
        if self.kind == "manual" and self.data.get("state") == "out":
            text = self.data.get("text")
            if text is not None and (not isinstance(text, str) or len(text.strip()) > 120):
                raise ValueError("出游描述须为最多 120 字的文字")
            if "minutes" in self.data:
                minutes = self.data["minutes"]
                if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 1440:
                    raise ValueError("出游时长须为 1～1440 分钟")
        return self


class SignalBatch(BaseModel):
    signals: list[SignalIn] = Field(max_length=500)


class Hub:
    """SSE 订阅者；每个连接一个小队列，满了就丢掉最旧的消息。队列里收到 None 表示该结束连接了。"""

    def __init__(self, limit: int):
        self.limit = limit
        self.queues: set[asyncio.Queue[str | None]] = set()
        self.closed = False

    def subscribe(self) -> asyncio.Queue[str | None] | None:
        if self.closed or len(self.queues) >= self.limit:
            return None
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=8)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str | None]) -> None:
        self.queues.discard(q)

    def close(self) -> None:
        """服务器要关闭了：让所有推送连接自己结束，uvicorn 就不用一直等它们断开。"""
        self.closed = True
        self.publish(None)

    def publish(self, msg: str | None) -> None:
        for q in self.queues:
            if q.full():
                q.get_nowait()
            q.put_nowait(msg)


class Presence:
    """把引擎、数据库和推送连在一起。所有方法都在事件循环线程里调用。"""

    def __init__(self, settings: Settings, rules: Rules, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.rules = rules
        self.clock = clock
        self.db = DB(settings.db_path)
        now = clock()
        self.engine = Engine(rules, settings.timing, started_at=now)
        self.hub = Hub(settings.max_streams)
        self.rate: dict[str, deque[float]] = {}
        self.last_payload = ""
        # 视频信息：(site, id) → 数据库里的一行；查询函数可替换，测试时不联网
        self.video_meta: dict[tuple[str, str], Any] = {}
        self.video_fetching: set[tuple[str, str]] = set()
        self.fetch_video_meta: Callable[[str, str], video.VideoMeta | None] = video.fetch_meta
        self.fetch_cover: Callable[[str, int], bytes | None] = video.fetch_cover
        # 网站图标：域名 → 数据库里的一行
        self.site_icons: dict[str, Any] = {}
        self.icon_fetching: set[str] = set()
        self.fetch_icon: Callable[[str, int], web.Icon | None] = web.fetch_icon
        # Steam：上次查到的游戏；游戏封面 appid → sha256（None 表示没取到，记下时间稍后重试）
        self.fetch_steam: Callable[[str, str], steam.Playing | None] = steam.fetch_playing
        self.steam_playing: steam.Playing | None = None
        self.game_covers: dict[str, tuple[str | None, float]] = {}
        self.cover_fetching: set[str] = set()
        self.tasks: set[asyncio.Task] = set()

        last = self.db.last_signal_ts()
        n = self.db.close_dangling(last if last is not None else now)
        if n:
            log.info("截止了 %d 个上次未结束的片段", n)
        self.db.end_stale_manual(now)
        self.db.recover_outings()
        for m in self.db.active_manual(now):
            self.engine.restore_manual(m)
        self.engine.set_courses(self.db.load_courses(), now)
        self.persist()

    # -- 设备

    def device_for(self, token: str) -> str | None:
        for name, expected in self.settings.devices.items():
            if hmac.compare_digest(token.encode(), expected.encode()):
                return name
        return None

    def allow(self, source: str, now: float) -> bool:
        q = self.rate.setdefault(source, deque())
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= self.settings.rate_per_minute:
            return False
        q.append(now)
        return True

    def handle(self, source: str, signals: list[SignalIn]) -> int:
        now = self.clock()
        rows = []
        for s in signals:
            ts = now if s.ts is None else min(s.ts, now)  # 设备时钟超前时按服务器时间算
            rows.append((source, s.kind, ts, s.ttl, s.data))
        rows.sort(key=lambda r: r[2])
        self.db.insert_signals(rows, now)
        for src, kind, ts, ttl, data in rows:
            self.db.record_web(src, kind, ts, ttl, data, self.engine.timing.away_sec)
            self.engine.ingest(src, kind, ts, ttl, data)
            if kind == "video" and not data.get("private") and isinstance(data.get("site"), str) and isinstance(data.get("id"), str):
                self.want_video_meta(data["site"], data["id"])
            if kind == "desktop" and not (data.get("private") or data.get("hidden")) and isinstance(data.get("domain"), str) and public_host(data["domain"].lower()):
                self.want_site_icon(data["domain"].lower())
        self.update(now)
        return len(rows)

    # -- 视频信息

    def cached_video_meta(self, site: str, vid: str) -> Any:
        key = (site, vid)
        if key not in self.video_meta:
            self.video_meta[key] = self.db.get_video_meta(site, vid)
        return self.video_meta[key]

    def want_video_meta(self, site: str, vid: str) -> None:
        """还没查过（或上次没查到且已过重试间隔）就在后台查一次，查到后推送新状态。"""
        key = (site, vid)
        row = self.cached_video_meta(site, vid)
        if key in self.video_fetching or site not in ("bilibili", "youtube") or len(vid) > 40:
            return
        if row is not None and (row["title"] is not None or self.clock() - row["fetched_at"] < VIDEO_META_RETRY_SEC):
            return
        self.video_fetching.add(key)
        self.spawn(self.load_video_meta(site, vid))

    def spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def save_fetched_image(self, data: bytes | None, source: str, origin_url: str | None = None,
                           mime: str | None = None) -> str | None:
        """把抓到的图片存进封面缓存，返回 sha256；不是图片时返回 None。mime 只在调用方已经检查过内容时传入。"""
        mime = mime or (sniff_image(data) if data else None)
        if not data or not mime:
            return None
        h = hashlib.sha256(data).hexdigest()
        self.save_media(h, data, mime, source, origin_url=origin_url)
        return h

    async def load_video_meta(self, site: str, vid: str) -> None:
        try:
            meta = await asyncio.to_thread(self.fetch_video_meta, site, vid)
            cover_hash = None
            if meta and meta.cover_url:
                data = await asyncio.to_thread(self.fetch_cover, meta.cover_url, self.settings.media_max_bytes)
                cover_hash = self.save_fetched_image(data, site, meta.cover_url)
            now = self.clock()
            if meta:
                self.db.save_video_meta(site, vid, now, meta.title, meta.owner, meta.url, cover_hash, meta.parts)
                if meta.title:
                    self.db.fill_video_log(site, vid, meta.title, meta.owner)
                log.info("视频信息：%s/%s %s", site, vid, meta.title)
            else:
                self.db.save_video_meta(site, vid, now)
            self.video_meta.pop((site, vid), None)
            self.update(now)
        except Exception:
            log.exception("保存视频信息出错 %s/%s", site, vid)
        finally:
            self.video_fetching.discard((site, vid))

    # -- 网站图标

    def cached_site_icon(self, domain: str) -> Any:
        if domain not in self.site_icons:
            self.site_icons[domain] = self.db.get_site_icon(domain)
        return self.site_icons[domain]

    def want_site_icon(self, domain: str) -> None:
        if domain in self.icon_fetching or not DOMAIN.fullmatch(domain):
            return
        row = self.cached_site_icon(domain)
        if row is not None and (row["cover_hash"] or self.clock() - row["fetched_at"] < SITE_ICON_RETRY_SEC):
            return
        self.icon_fetching.add(domain)
        self.spawn(self.load_site_icon(domain))

    async def load_site_icon(self, domain: str) -> None:
        try:
            icon = await asyncio.to_thread(self.fetch_icon, domain, self.settings.media_max_bytes)
            now = self.clock()
            h = self.save_fetched_image(icon.data, "icon", icon.url, icon.mime) if icon else None
            self.db.save_site_icon(domain, h, now)
            self.site_icons.pop(domain, None)
            self.update(now)
        except Exception:
            log.exception("保存网站图标出错 %s", domain)
        finally:
            self.icon_fetching.discard(domain)

    # -- Steam

    async def poll_steam(self) -> float:
        """查一次 Steam，把开始 / 退出变成 game 信号；返回下次查询前等待的秒数。"""
        cfg = self.settings.steam
        try:
            cur = await asyncio.to_thread(self.fetch_steam, cfg.api_key, cfg.steam_id)
        except steam.SteamError as exc:
            log.warning("查询 Steam 失败：%s", exc)
            return cfg.playing_poll_sec if self.steam_playing else cfg.poll_sec
        prev, signals = self.steam_playing, []
        if prev and (cur is None or cur.name != prev.name):
            signals.append(SignalIn(kind="game", ttl=1, data={"name": prev.name, "appid": prev.appid, "event": "stop"}))
            log.info("Steam：退出《%s》", prev.name)
        if cur:
            # 每次都发 start 续期；连续几次查询失败（约 5 倍间隔）后自动当作结束
            ttl = cfg.playing_poll_sec * 5
            signals.append(SignalIn(kind="game", ttl=ttl, data={"name": cur.name, "appid": cur.appid, "event": "start"}))
            if prev is None or cur.name != prev.name:
                log.info("Steam：开始玩《%s》（appid %s）", cur.name, cur.appid)
            if cur.appid:
                self.want_game_cover(cur.appid)
        self.steam_playing = cur
        if signals:
            self.handle("steam", signals)
        return cfg.playing_poll_sec if cur else cfg.poll_sec

    def want_game_cover(self, appid: str) -> None:
        if appid in self.cover_fetching:
            return
        if appid not in self.game_covers:
            self.game_covers[appid] = (self.db.media_by_origin(steam.cover_url(appid)), 0)
        h, tried = self.game_covers[appid]
        if h or self.clock() - tried < STEAM_RETRY_SEC:
            return
        self.cover_fetching.add(appid)
        self.spawn(self.load_game_cover(appid))

    async def load_game_cover(self, appid: str) -> None:
        url = steam.cover_url(appid)
        try:
            data = await asyncio.to_thread(self.fetch_cover, url, self.settings.media_max_bytes)
            self.game_covers[appid] = (self.save_fetched_image(data, "steam", url), self.clock())
            self.update()
        except Exception:
            log.exception("保存游戏封面出错 %s", appid)
        finally:
            self.cover_fetching.discard(appid)

    def game_card(self, card: dict[str, Any]) -> None:
        appid = card.get("appid")
        h = self.game_covers.get(appid, (None, 0))[0] if isinstance(appid, str) else None
        if h:
            card["cover_hash"] = h

    def web_card(self, card: dict[str, Any]) -> None:
        domain = card.get("domain")
        row = self.cached_site_icon(domain) if isinstance(domain, str) else None
        if row is not None and row["cover_hash"]:
            card["cover_hash"] = row["cover_hash"]

    def video_card(self, card: dict[str, Any]) -> None:
        """视频卡片补上标题、UP 主、链接和封面；还没查到时只有进度。"""
        site, vid = card.get("site"), card.get("id")
        row = self.cached_video_meta(site, vid) if isinstance(site, str) and isinstance(vid, str) else None
        if row is None or row["title"] is None:
            return
        title, url, page = row["title"], row["url"], card.get("page")
        parts = json.loads(row["parts_json"])
        if isinstance(page, (int, float)) and page >= 2:
            page = int(page)
            url += f"?p={page}"
            if len(parts) >= page and parts[page - 1] and parts[page - 1] != title:
                title += f" · P{page} {parts[page - 1]}"
        card.update(title=title, owner=row["owner"], url=url)
        if row["cover_hash"]:
            card["cover_hash"] = row["cover_hash"]

    # -- 封面

    def media_file(self, media_id: str) -> tuple[Path, str] | None:
        row = self.db.get_media(media_id)
        if row is None:
            return None
        path = self.settings.media_dir / row["path"]
        return (path, row["mime"]) if path.is_file() else None

    def save_media(self, media_id: str, data: bytes, mime: str, source: str, origin_url: str | None = None) -> bool:
        """保存一张图片；已经有了返回 False。先写临时文件再改名，避免留下半截文件。"""
        if self.media_file(media_id):
            return False
        d = self.settings.media_dir
        d.mkdir(parents=True, exist_ok=True)
        name = f"{media_id}.{MEDIA_EXT[mime]}"
        tmp = d / f".{name}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / name)
        self.db.add_media(media_id, mime, name, len(data), source, self.clock(), origin_url)
        return True

    # -- 状态

    def update(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.db.expire_web(now)
        self.engine.tick(now)
        self.persist()
        payload = self.payload(self.engine.snapshot(now))
        key = json.dumps({k: v for k, v in payload.items() if k != "updated_at"}, ensure_ascii=False, sort_keys=True)
        if key != self.last_payload:
            self.last_payload = key
            self.hub.publish(json.dumps(payload, ensure_ascii=False))

    def persist(self) -> None:
        ivs = self.engine.drain_intervals()
        self.db.save_intervals(ivs)
        for iv in ivs:
            m = iv.meta.get("media")
            if m and m["type"] == "video" and not iv.meta.get("private") and not m.get("title"):
                row = self.cached_video_meta(m["site"], m["vid"])
                if row is not None and row["title"] is not None:
                    m["title"], m["artist"] = row["title"], row["owner"]
        self.db.save_media_log(ivs)
        self.db.save_manual(self.engine.drain_manual())

    def payload(self, snap: Snapshot) -> dict[str, Any]:
        out = snap.public()
        items = [out["main"], *out["side"]]
        if any(i.get("tier") == 1 or i.get("activity") for i in items):
            totals = self.today(snap.updated_at)["totals"]["activity"]
            for i in items:
                cat = i.get("activity") or (i["category"] if i.get("tier") == 1 else None)
                if cat in totals:
                    i["today_sec"] = totals[cat]
        for i in items:
            card = i.get("card")
            if card and card.get("kind") == "web":
                self.web_card(card)
            elif card and i["category"] == "video":
                self.video_card(card)
            elif card and i["category"] == "game":
                self.game_card(card)
            if card and "cover_hash" in card:
                h = card.pop("cover_hash")
                if h:
                    card["cover"] = f"{self.settings.media_base}/{h}"
        return out

    def now(self) -> dict[str, Any]:
        t = self.clock()
        return self.payload(self.engine.snapshot(t))

    def today(self, now: float | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else now
        return stats.today(self.db, self.rules, now, self.settings.tz, self.settings.day_start_hour)

    # -- 课表

    async def refresh_courses(self) -> None:
        cal = self.settings.calendar
        if not cal.enabled:
            return
        try:
            courses = await asyncio.to_thread(fetch_courses, cal.name, cal.title_strip)
        except CalendarUnavailable as exc:
            log.warning("读不到课表，继续使用缓存：%s", exc)
            return
        except Exception:
            log.exception("读取课表出错，继续使用缓存")
            return
        now = self.clock()
        self.db.save_courses(courses, keep_from=now - 86400)
        self.engine.set_courses(courses, now)
        self.update(now)
        log.info("课表已刷新：未来 14 天共 %d 节", sum(c.start > now for c in courses))


async def _ticker(p: Presence) -> None:
    last_prune = 0.0
    while True:
        await asyncio.sleep(TICK_SEC)
        try:
            p.update()
            now = p.clock()
            if now - last_prune > 3600:
                p.db.prune_signals(now - SIGNAL_KEEP_DAYS * 86400)
                last_prune = now
        except Exception:
            log.exception("定时任务出错")


async def _calendar_loop(p: Presence) -> None:
    while True:
        await p.refresh_courses()
        await asyncio.sleep(p.settings.calendar.refresh_hours * 3600)


async def _steam_loop(p: Presence) -> None:
    while True:
        try:
            delay = await p.poll_steam()
        except Exception:
            log.exception("Steam 轮询出错")
            delay = p.settings.steam.poll_sec
        await asyncio.sleep(delay)


def _close_streams_on_exit(hub: Hub) -> Callable[[], None]:
    """在 uvicorn 的 Ctrl+C / kill 处理之前先结束推送连接；返回恢复原处理函数的函数。"""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    loop = asyncio.get_running_loop()
    originals = {}

    def handler(sig: int, frame: Any) -> None:
        loop.call_soon_threadsafe(hub.close)
        prev = originals[sig]
        if callable(prev):
            prev(sig, frame)

    for sig in (signal.SIGINT, signal.SIGTERM):
        originals[sig] = signal.signal(sig, handler)

    def restore() -> None:
        for sig, prev in originals.items():
            signal.signal(sig, prev)

    return restore


def create_app(
    settings: Settings | None = None,
    rules: Rules | None = None,
    clock: Callable[[], float] = time.time,
    background: bool = True,
) -> FastAPI:
    settings = settings or Settings.load()
    rules = rules or Rules.load(CONFIG_DIR / "rules.yaml")
    state: dict[str, Presence] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = Presence(settings, rules, clock)
        state["p"] = p
        app.state.presence = p
        tasks = [asyncio.create_task(_ticker(p)), asyncio.create_task(_calendar_loop(p))] if background else []
        if background and settings.steam.enabled:
            tasks.append(asyncio.create_task(_steam_loop(p)))
        restore = _close_streams_on_exit(p.hub)
        try:
            yield
        finally:
            restore()
            for t in [*tasks, *p.tasks]:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            p.persist()
            p.db.close()

    app = FastAPI(title="Presence", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    api = APIRouter(prefix=PREFIX)
    public_origins = set(settings.public_origins)

    @app.middleware("http")
    async def cors(request: Request, call_next):
        origin = request.headers.get("origin")
        path = request.url.path
        public = path.startswith(PREFIX) and not path.startswith(f"{PREFIX}/private") and path != f"{PREFIX}/signal"
        allowed = public and origin in public_origins
        if request.method == "OPTIONS" and allowed:
            resp: Response = Response(status_code=204)
            resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            resp.headers["Access-Control-Max-Age"] = "86400"
        else:
            resp = await call_next(request)
        if allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers.setdefault("Vary", "Origin")
        return resp

    def device(request: Request) -> str:
        """检查设备令牌和频率限制，返回设备名（即信号来源）。"""
        p = state["p"]
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        source = p.device_for(token) if token else None
        if source is None:
            raise HTTPException(401, "设备令牌无效")
        if not p.allow(source, p.clock()):
            raise HTTPException(429, "上报太频繁")
        return source

    @api.post("/signal")
    async def signal(request: Request, body: SignalBatch | SignalIn):
        source = device(request)
        signals = body.signals if isinstance(body, SignalBatch) else [body]
        return {"ok": True, "accepted": state["p"].handle(source, signals)}

    @api.put("/media/{media_id}")
    async def put_media(media_id: str, request: Request):
        source = device(request)
        p = state["p"]
        if not MEDIA_ID.fullmatch(media_id):
            raise HTTPException(400, "地址里应是 64 位小写十六进制的 sha256")
        limit = p.settings.media_max_bytes
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, f"图片不能超过 {limit} 字节")
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > limit:
                raise HTTPException(413, f"图片不能超过 {limit} 字节")
        data = bytes(buf)
        mime = sniff_image(data)
        if mime is None:
            raise HTTPException(415, "只接受 JPEG、PNG、WebP 图片")
        if hashlib.sha256(data).hexdigest() != media_id:
            raise HTTPException(400, "sha256 与内容不一致")
        created = p.save_media(media_id, data, mime, source)
        return JSONResponse({"ok": True, "created": created}, status_code=201 if created else 200)

    @api.api_route("/media/{media_id}", methods=["GET", "HEAD"])
    async def get_media(media_id: str):
        found = state["p"].media_file(media_id) if MEDIA_ID.fullmatch(media_id) else None
        if found is None:
            raise HTTPException(404, "没有这张图片", headers={"Cache-Control": "no-store"})
        path, mime = found
        return FileResponse(path, media_type=mime, headers=MEDIA_HEADERS)

    @api.get("/now")
    async def now():
        return JSONResponse(state["p"].now(), headers={"Cache-Control": "no-store"})

    @api.get("/today")
    async def today():
        return JSONResponse(state["p"].today(), headers={"Cache-Control": "no-store"})

    @api.get("/health")
    async def health():
        p = state["p"]
        return {"ok": True, "time": p.clock(), "streams": len(p.hub.queues)}

    @api.get("/stream")
    async def stream(request: Request):
        p = state["p"]
        q = p.hub.subscribe()
        if q is None:
            raise HTTPException(503, "连接数已满")

        async def events():
            try:
                yield f"event: now\ndata: {json.dumps(p.now(), ensure_ascii=False)}\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), SSE_PING_SEC)
                        if msg is None:
                            return
                        yield f"event: now\ndata: {msg}\n\n"
                    except TimeoutError:
                        yield ": ping\n\n"  # 心跳，防止 Cloudflare 断开空闲连接
            finally:
                p.hub.unsubscribe(q)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.include_router(api)
    return app


def __getattr__(name: str):
    # `uvicorn presence.main:app` 时才读取配置，导入模块本身（例如测试）不需要 settings.toml
    if name == "app":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        return create_app()
    raise AttributeError(name)
