"""按 B站 BV 号（番剧 ep / ss 号）、YouTube 视频 ID 查标题、UP 主 / 频道和封面地址。

都是同步网络请求，由调用方放到线程里执行。查不到（视频不存在、接口风控、网络失败）时返回 None。
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import truststore

log = logging.getLogger("presence.video")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
TIMEOUT = 10
BILI_COVER = "@480w_270h_1c.jpg"  # B站图片服务按后缀缩放并转成 JPEG，封面不用存原图
# 用系统钥匙串里的证书校验 HTTPS：代理软件（如 Quantumult X）解密 YouTube 时用的是它自己装进钥匙串的证书，
# Python 自带的证书列表里没有
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


@dataclass
class VideoMeta:
    title: str
    owner: str | None
    url: str
    cover_url: str | None = None
    parts: list[str] = field(default_factory=list)  # B站分 P 标题，按 P 顺序；番剧为各集标题


def _get(url: str, *, referer: str | None = None, limit: int | None = None) -> bytes:
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT,
                                context=SSL_CONTEXT) as resp:
        data = resp.read(limit + 1 if limit else -1)
    if limit and len(data) > limit:
        raise ValueError(f"超过 {limit} 字节")
    return data


def _json(url: str) -> Any:
    return json.loads(_get(url, referer="https://www.bilibili.com/"))


def _https(url: str | None) -> str | None:
    if not url:
        return None
    return "https:" + url[5:] if url.startswith("http:") else url


def _bili_video(vid: str) -> VideoMeta | None:
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", vid):
        q = {"bvid": vid}
    elif m := re.fullmatch(r"av(\d+)", vid, re.I):
        q = {"aid": m[1]}
    else:
        return None
    r = _json("https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(q))
    if r.get("code") != 0 or not (d := r.get("data")):
        log.info("B站查不到视频 %s：%s", vid, r.get("message"))
        return None
    pic = _https(d.get("pic"))
    return VideoMeta(
        title=d.get("title") or vid,
        owner=(d.get("owner") or {}).get("name"),
        url=f"https://www.bilibili.com/video/{d.get('bvid') or vid}",
        cover_url=pic + BILI_COVER if pic else None,
        parts=[p.get("part") or "" for p in d.get("pages") or []],
    )


def _bili_bangumi(vid: str) -> VideoMeta | None:
    m = re.fullmatch(r"(ep|ss)(\d+)", vid)
    if not m:
        return None
    key = "ep_id" if m[1] == "ep" else "season_id"
    r = _json(f"https://api.bilibili.com/pgc/view/web/season?{key}={m[2]}")
    if r.get("code") != 0 or not (d := r.get("result")):
        log.info("B站查不到番剧 %s：%s", vid, r.get("message"))
        return None
    title = d.get("title") or vid
    if m[1] == "ep":
        ep = next((e for e in d.get("episodes") or [] if str(e.get("id")) == m[2]), None)
        if ep:
            # “西游记 第1话 猴王初问世”
            num = ep.get("title") or ""
            title += (f" 第{num}话" if num.isdigit() else f" {num}") + (f" {ep['long_title']}" if ep.get("long_title") else "")
    cover = _https(d.get("cover"))
    return VideoMeta(
        title=title.strip(),
        owner=None,
        url=f"https://www.bilibili.com/bangumi/play/{vid}",
        cover_url=cover + BILI_COVER if cover else None,
    )


def _youtube(vid: str) -> VideoMeta | None:
    if not re.fullmatch(r"[0-9A-Za-z_-]{6,20}", vid):
        return None
    url = f"https://www.youtube.com/watch?v={vid}"
    r = json.loads(_get("https://www.youtube.com/oembed?" + urllib.parse.urlencode({"url": url, "format": "json"})))
    return VideoMeta(
        title=r.get("title") or vid,
        owner=r.get("author_name"),
        url=url,
        cover_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
    )


def fetch_meta(site: str, vid: str) -> VideoMeta | None:
    try:
        if site == "bilibili":
            return _bili_bangumi(vid) if vid[:2] in ("ep", "ss") else _bili_video(vid)
        if site == "youtube":
            return _youtube(vid)
    except Exception as exc:  # 网络失败、接口改版：只记日志，卡片降级为只显示进度
        log.warning("查询视频信息失败 %s/%s：%s", site, vid, exc)
    return None


def fetch_cover(url: str, max_bytes: int) -> bytes | None:
    try:
        return _get(url, referer="https://www.bilibili.com/", limit=max_bytes)
    except Exception as exc:
        log.warning("下载封面失败 %s：%s", url, exc)  # 游戏封面也走这里
        return None
