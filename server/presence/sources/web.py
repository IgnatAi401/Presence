"""网站图标：优先用网站自己在首页里声明的图标，和 Safari 地址栏里看到的一致。

顺序：深色版 SVG → SVG → PNG（尺寸大的优先）→ apple-touch-icon → ICO（用 macOS 的 sips 转成 PNG）
→ /favicon.ico → Google 图标服务。每一步下载后都检查内容，不是图片就试下一个。
都是同步网络请求，由调用方放到线程里执行。
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from .video import SSL_CONTEXT, TIMEOUT, UA

log = logging.getLogger("presence.web")

HTML_LIMIT = 512 * 1024  # 图标声明都在 <head> 里，首页只读开头这么多
MAX_TRIES = 6


@dataclass
class Icon:
    data: bytes
    mime: str  # image/svg+xml、image/png、image/jpeg、image/webp
    url: str


def _open(url: str, limit: int, truncate: bool = False) -> tuple[bytes, str]:
    """返回 (内容, 跳转后的地址)；超过 limit 字节时抛错，truncate 时只取前 limit 字节。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CONTEXT) as resp:
        data = resp.read(limit + 1)
        final = resp.geturl()
    if len(data) > limit:
        if not truncate:
            raise ValueError(f"超过 {limit} 字节")
        data = data[:limit]
    return data, final


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            self.links.append({k.lower(): v or "" for k, v in attrs})


def _size(sizes: str) -> int:
    best = 0
    for s in sizes.lower().split():
        if s == "any":
            return 1000
        if m := re.fullmatch(r"(\d+)x(\d+)", s):
            best = max(best, int(m[1]))
    return best


def candidates(html: str, base: str) -> list[str]:
    """按优先顺序列出首页里声明的图标地址（已转成绝对地址、去重），最后补上 /favicon.ico。"""
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:  # 残缺的 HTML：用已经解析出来的部分
        pass
    ranked: list[tuple[int, int, int, str]] = []
    for i, link in enumerate(parser.links):
        rel = set(link.get("rel", "").lower().split())
        href = link.get("href", "").strip()
        if not href or href.startswith("data:") or not (rel & {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}):
            continue
        url = urllib.parse.urljoin(base, href)
        typ = link.get("type", "").lower()
        path = urllib.parse.urlparse(url).path.lower()
        dark = "dark" in link.get("media", "").lower()
        size = _size(link.get("sizes", ""))
        if rel & {"apple-touch-icon", "apple-touch-icon-precomposed"}:
            ranked.append((4, -size, i, url))
        elif "svg" in typ or path.endswith(".svg"):
            if dark:
                ranked.append((0, 0, i, url))
            else:
                if path.endswith(".svg"):
                    # 很多网站（例如 GitHub）的深色版图标和浅色版同名，只多一个 -dark，由页面脚本切换
                    p = urllib.parse.urlparse(url)
                    ranked.append((1, 0, i, p._replace(path=p.path[:-4] + "-dark.svg").geturl()))
                ranked.append((2, 0, i, url))
        elif "icon" in typ or path.endswith(".ico"):
            ranked.append((5, -size, i, url))
        else:
            ranked.append((3, -size, i, url))
    ranked.append((6, 0, 0, urllib.parse.urljoin(base, "/favicon.ico")))
    out: list[str] = []
    for *_, url in sorted(ranked):
        if url not in out and url.startswith(("https://", "http://")):
            out.append(url)
    return out


def sniff(data: bytes) -> str | None:
    """按内容判断格式：位图、SVG，或需要转换的 ICO（返回 image/x-icon）。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if (head.startswith((b"<svg", b"<?xml", b"<!--")) and b"<svg" in head) and b"<html" not in head:
        return "image/svg+xml"
    return None


def ico_to_png(data: bytes) -> bytes | None:
    """用 macOS 自带的 sips 把 ICO 转成 PNG；不是 macOS 或转换失败时返回 None。"""
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "icon.ico", Path(d) / "icon.png"
        src.write_bytes(data)
        try:
            subprocess.run(["/usr/bin/sips", "-s", "format", "png", str(src), "--out", str(dst)],
                           check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        out = dst.read_bytes() if dst.exists() else b""
    return out if out.startswith(b"\x89PNG") else None


def _download(url: str, max_bytes: int) -> Icon | None:
    try:
        data, final = _open(url, max_bytes)
    except Exception as exc:
        log.info("图标下载失败 %s：%r", url, exc)
        return None
    mime = sniff(data)
    if mime == "image/x-icon":
        data, mime = ico_to_png(data), "image/png"
        if data is None:
            log.info("ICO 转 PNG 失败 %s", url)
            return None
    if not mime:
        log.info("不是图片 %s：%r", url, data[:40])
        return None
    return Icon(data, mime, final)


def fetch_icon(domain: str, max_bytes: int) -> Icon | None:
    tried = 0
    try:
        html, base = _open(f"https://{domain}/", HTML_LIMIT, truncate=True)
        urls = candidates(html.decode("utf-8", "replace"), base)
    except Exception as exc:
        log.info("读取 %s 首页失败，改试 favicon.ico：%s", domain, exc)
        urls = []
    for url in urls or [f"https://{domain}/favicon.ico"]:
        if tried >= MAX_TRIES:
            break
        tried += 1
        if icon := _download(url, max_bytes):
            return icon
    google = "https://www.google.com/s2/favicons?" + urllib.parse.urlencode({"domain": domain, "sz": 64})
    try:
        data, _ = _open(google, max_bytes)
        if (mime := sniff(data)) in ("image/png", "image/jpeg", "image/webp"):
            return Icon(data, mime, google)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:  # 404：Google 也没有这个网站的图标
            log.warning("获取网站图标失败 %s：%s", domain, exc)
    except Exception as exc:
        log.warning("获取网站图标失败 %s：%s", domain, exc)
    return None
