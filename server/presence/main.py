"""应用入口：信号接收、公开接口、SSE 推送和定时任务。

启动：uv run uvicorn presence.main:app --host 127.0.0.1 --port 8790
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import stats
from .config import CONFIG_DIR, Settings
from .db import DB
from .engine import Engine, Snapshot
from .rules import Rules
from .sources.calendar import CalendarUnavailable, fetch_courses

log = logging.getLogger("presence")

PREFIX = "/presence/api"
TICK_SEC = 15
SSE_PING_SEC = 25
SIGNAL_KEEP_DAYS = 30


class SignalIn(BaseModel):
    kind: Literal["desktop", "video", "music", "manual", "phone_app", "game", "heartbeat"]
    ts: float | None = None
    ttl: float = Field(180, gt=0, le=86400)
    data: dict[str, Any] = Field(default_factory=dict)


class SignalBatch(BaseModel):
    signals: list[SignalIn] = Field(max_length=500)


class Hub:
    """SSE 订阅者；每个连接一个小队列，满了就丢掉最旧的消息。"""

    def __init__(self, limit: int):
        self.limit = limit
        self.queues: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str] | None:
        if len(self.queues) >= self.limit:
            return None
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self.queues.discard(q)

    def publish(self, msg: str) -> None:
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

        last = self.db.last_signal_ts()
        n = self.db.close_dangling(last if last is not None else now)
        if n:
            log.info("截止了 %d 个上次未结束的片段", n)
        self.db.end_stale_manual(now)
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
            self.engine.ingest(src, kind, ts, ttl, data)
        self.update(now)
        return len(rows)

    # -- 状态

    def update(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.engine.tick(now)
        self.persist()
        payload = self.payload(self.engine.snapshot(now))
        key = json.dumps({k: v for k, v in payload.items() if k != "updated_at"}, ensure_ascii=False, sort_keys=True)
        if key != self.last_payload:
            self.last_payload = key
            self.hub.publish(json.dumps(payload, ensure_ascii=False))

    def persist(self) -> None:
        self.db.save_intervals(self.engine.drain_intervals())
        self.db.save_manual(self.engine.drain_manual())

    def payload(self, snap: Snapshot) -> dict[str, Any]:
        out = snap.public()
        items = [out["main"], *out["side"]]
        if any(i.get("tier") == 1 for i in items):
            totals = self.today(snap.updated_at)["totals"]["activity"]
            for i in items:
                if i.get("tier") == 1 and i["category"] in totals:
                    i["today_sec"] = totals[i["category"]]
        for i in items:
            card = i.get("card")
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
        try:
            yield
        finally:
            for t in tasks:
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

    @api.post("/signal")
    async def signal(request: Request, body: SignalBatch | SignalIn):
        p = state["p"]
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        source = p.device_for(token) if token else None
        if source is None:
            raise HTTPException(401, "设备令牌无效")
        if not p.allow(source, p.clock()):
            raise HTTPException(429, "上报太频繁")
        signals = body.signals if isinstance(body, SignalBatch) else [body]
        return {"ok": True, "accepted": p.handle(source, signals)}

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
