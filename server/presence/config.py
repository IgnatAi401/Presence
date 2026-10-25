"""读取 settings.toml（含设备令牌，不进 Git）。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import Timing

SERVER_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = SERVER_DIR / "config"


@dataclass
class CalendarSettings:
    enabled: bool = False
    name: str = "课程表"
    title_strip: str | None = None  # 正则：从事件标题里去掉的部分（教室、教师等）
    refresh_hours: float = 6


@dataclass
class SteamSettings:
    api_key: str = ""  # Steam Web API Key（32 位）：https://steamcommunity.com/dev/apikey
    steam_id: str = ""  # SteamID64（17 位数字）
    poll_sec: float = 60
    playing_poll_sec: float = 30  # 在玩时查得勤一些，及时发现退出

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.steam_id)


@dataclass
class Settings:
    devices: dict[str, str]  # 设备名（即信号来源 source）→ 令牌
    timezone: str = "Asia/Shanghai"
    day_start_hour: int = 4
    db_path: Path = SERVER_DIR / "data" / "presence.db"
    public_origins: list[str] = field(default_factory=lambda: ["https://now.ignat.ai", "https://ignat.ai"])
    media_base: str = "https://api.ignat.ai/presence/api/media"
    media_max_bytes: int = 2 * 1024 * 1024  # 设备上传封面的大小上限
    rate_per_minute: int = 60
    max_streams: int = 200
    timing: Timing = field(default_factory=Timing)
    calendar: CalendarSettings = field(default_factory=CalendarSettings)
    steam: SteamSettings = field(default_factory=SteamSettings)

    @property
    def media_dir(self) -> Path:
        """封面等图片文件放在数据库旁边的 media/ 目录。"""
        return self.db_path.parent / "media"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or Path(os.environ.get("PRESENCE_SETTINGS", CONFIG_DIR / "settings.toml"))
        if not path.exists():
            raise SystemExit(f"找不到配置文件 {path}：请复制 config/settings.example.toml 为 settings.toml 并填写设备令牌")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        server = raw.get("server", {})
        db_path = Path(server.get("db_path", "data/presence.db"))
        if not db_path.is_absolute():
            db_path = SERVER_DIR / db_path
        timing_keys = {f.name for f in fields(Timing)}
        cal_keys = {f.name for f in fields(CalendarSettings)}
        steam_keys = {f.name for f in fields(SteamSettings)}
        devices = {k: v for k, v in raw.get("devices", {}).items() if v}
        return cls(
            devices=devices,
            timezone=server.get("timezone", "Asia/Shanghai"),
            day_start_hour=int(server.get("day_start_hour", 4)),
            db_path=db_path,
            public_origins=list(server.get("public_origins", ["https://now.ignat.ai", "https://ignat.ai"]))
            + list(server.get("extra_origins", [])),
            media_base=server.get("media_base", "https://api.ignat.ai/presence/api/media"),
            media_max_bytes=int(server.get("media_max_bytes", 2 * 1024 * 1024)),
            rate_per_minute=int(server.get("rate_per_minute", 60)),
            max_streams=int(server.get("max_streams", 200)),
            timing=Timing(**{k: v for k, v in raw.get("engine", {}).items() if k in timing_keys}),
            calendar=CalendarSettings(**{k: v for k, v in raw.get("calendar", {}).items() if k in cal_keys}),
            steam=SteamSettings(**{k: str(v) if k in ("api_key", "steam_id") else v
                                   for k, v in raw.get("steam", {}).items() if k in steam_keys}),
        )
