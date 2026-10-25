"""网站图标：首页里图标声明的优先顺序、格式判断、ICO 转换（不联网）。"""

import shutil
import struct
import zlib

import pytest

from presence.sources.web import candidates, ico_to_png, sniff

GITHUB = """<html><head>
<link rel="fluid-icon" href="https://github.com/fluidicon.png">
<link rel="mask-icon" href="https://github.githubassets.com/pinned-octocat.svg">
<link rel="alternate icon" type="image/png" href="https://github.githubassets.com/favicons/favicon.png">
<link rel="icon" type="image/svg+xml" href="https://github.githubassets.com/favicons/favicon.svg">
</head></html>"""

MIXED = """<head>
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="120x120" href="/t120.png">
<link rel="apple-touch-icon" sizes="180x180" href="/t180.png">
<link rel="icon" type="image/png" sizes="16x16" href="/f16.png">
<link rel="icon" type="image/png" sizes="32x32" href="/f32.png">
<link rel="icon" media="(prefers-color-scheme: dark)" href="icons/dark.svg">
<link rel="icon" href="data:image/png;base64,AAAA">
"""


def test_github_dark_svg_first():
    assert candidates(GITHUB, "https://github.com/") == [
        "https://github.githubassets.com/favicons/favicon-dark.svg",  # 猜出来的深色版
        "https://github.githubassets.com/favicons/favicon.svg",
        "https://github.githubassets.com/favicons/favicon.png",
        "https://github.com/favicon.ico",
    ]


def test_order_svg_png_touch_ico():
    assert candidates(MIXED, "https://example.com/a/") == [
        "https://example.com/a/icons/dark.svg",
        "https://example.com/f32.png",
        "https://example.com/f16.png",
        "https://example.com/t180.png",
        "https://example.com/t120.png",
        "https://example.com/favicon.ico",
    ]


def test_no_links_falls_back_to_favicon_ico():
    assert candidates("<html>没有图标</html>", "https://sspai.com/") == ["https://sspai.com/favicon.ico"]


def test_sniff():
    assert sniff(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert sniff(b"\x00\x00\x01\x00\x01\x00") == "image/x-icon"
    assert sniff(b'\xef\xbb\xbf<?xml version="1.0"?>\n<svg xmlns="x"></svg>') == "image/svg+xml"
    assert sniff(b"<svg width='32'></svg>") == "image/svg+xml"
    assert sniff(b"<!doctype html><html><body><svg></svg></body></html>") is None  # 软 404 页面
    assert sniff(b"GIF89a") is None


def _png(n=16) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xff\xff\xff\xff" * n for _ in range(n))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@pytest.mark.skipif(not shutil.which("sips"), reason="只有 macOS 有 sips")
def test_ico_to_png():
    png = _png()
    # 内嵌 PNG 的 ICO：文件头 + 一条目录项 + PNG 数据
    ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, len(png), 22) + png
    out = ico_to_png(ico)
    assert out is not None and out.startswith(b"\x89PNG")
    assert ico_to_png(b"not an icon") is None
