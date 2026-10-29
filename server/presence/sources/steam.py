"""用 Steam Web API 查当前在玩的游戏（ISteamUser/GetPlayerSummaries/v2）。

需要 Steam 隐私设置里“游戏详情”为公开，否则接口不返回 gameid。
同步网络请求，由调用方放到线程里执行。
单独测试：uv run python -m presence.sources.steam（读取 config/settings.toml）
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import truststore

log = logging.getLogger("presence.steam")

API = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
STORE_API = "https://store.steampowered.com/api/appdetails"
TIMEOUT = 10
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
APPID = re.compile(r"\d{1,10}")  # 非 Steam 游戏快捷方式的 gameid 是很大的 64 位数，没有商店页和封面


class SteamError(RuntimeError):
    """查询失败（密钥错误、网络失败、接口改版）；错误信息里不带密钥。"""


@dataclass
class Playing:
    name: str
    appid: str | None  # 商店里的游戏 ID；非 Steam 游戏为 None


def cover_url(appid: str) -> str:
    """旧式固定封面地址；较新的游戏在这里没有图（404），只当缓存键和兜底用。"""
    return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"


def parse_header(resp: dict, appid: str) -> str | None:
    entry = resp.get(appid) or {}
    url = (entry.get("data") or {}).get("header_image") if entry.get("success") else None
    return url if isinstance(url, str) and url.startswith("https://") else None


def fetch_cover_url(appid: str) -> str:
    """从商店接口查封面地址（新游戏的路径里带哈希，拼不出来）；查不到时退回旧式地址。"""
    url = STORE_API + "?" + urllib.parse.urlencode({"appids": appid, "filters": "basic"})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT, context=SSL_CONTEXT) as r:
            header = parse_header(json.loads(r.read(1 << 20)), appid)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("查询游戏封面地址失败 %s：%s", appid, getattr(exc, "reason", exc))
        header = None
    return header or cover_url(appid)


def parse(resp: dict) -> Playing | None:
    players = (resp.get("response") or {}).get("players")
    if not isinstance(players, list):
        raise SteamError("接口返回格式不对")
    if not players:
        raise SteamError("查不到这个 SteamID64")
    p = players[0]
    gameid = str(p.get("gameid") or "")
    name = p.get("gameextrainfo")
    if not gameid and not name:
        return None
    appid = gameid if APPID.fullmatch(gameid) and int(gameid) < 2**32 else None
    return Playing(name=str(name or f"App {gameid}"), appid=appid)


def fetch_playing(api_key: str, steam_id: str) -> Playing | None:
    """返回正在玩的游戏；没在玩返回 None；查询失败抛 SteamError。"""
    url = API + "?" + urllib.parse.urlencode({"key": api_key, "steamids": steam_id})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT, context=SSL_CONTEXT) as r:
            resp = json.loads(r.read(1 << 20))
    except urllib.error.HTTPError as exc:
        hint = "：API Key 不对或已失效" if exc.code in (401, 403) else ""
        raise SteamError(f"HTTP {exc.code}{hint}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SteamError(f"网络失败：{getattr(exc, 'reason', exc)}") from None
    except ValueError:
        raise SteamError("接口返回的不是 JSON") from None
    return parse(resp)


if __name__ == "__main__":
    from ..config import Settings  # noqa: PLC0415

    s = Settings.load().steam
    if not (s.api_key and s.steam_id):
        print("settings.toml 的 [steam] 里还没填 api_key 和 steam_id")
        sys.exit(1)
    try:
        g = fetch_playing(s.api_key, s.steam_id)
    except SteamError as exc:
        print("查询失败：", exc)
        sys.exit(1)
    print(f"正在玩：{g.name}（appid {g.appid or '无'}）" if g else "连接正常，当前没在玩游戏（或“游戏详情”不是公开）")
