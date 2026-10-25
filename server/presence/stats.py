"""统计查询：一天的边界、公开时间线（由两条线推导主状态）、各类别时长。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .db import DB
from .engine import ANCHOR
from .rules import Rules


def day_bounds(now: float, tz: ZoneInfo, start_hour: int) -> tuple[float, float, str]:
    """返回 (当天开始, 当天结束, 日期字符串)；凌晨 start_hour 点之前仍算前一天。"""
    local = datetime.fromtimestamp(now, tz)
    day = (local - timedelta(hours=start_hour)).date()
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp(), day.isoformat()


def _clip(rows, a: float, b: float, now: float) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        s, e = max(r["start"], a), min(r["end"] if r["end"] is not None else now, b, now)
        if e > s:
            out.append({**dict(r), "start": s, "end": e})
    return out


def totals(segs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """每条线、每个类别分别计时；正事的摸鱼时段不计入。
    不同类别可以重叠（边听歌边科研）；同一类别重叠的部分只算一次
    （例如 Safari 前台在 B站 和小工具上报的视频详情同时记了“看视频”）。"""
    spans: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for s in segs:
        if not s["slacking"]:
            spans.setdefault((s["line"], s["category"]), []).append((s["start"], s["end"]))
    out: dict[str, dict[str, float]] = {"anchor": {}, "activity": {}}
    for (line, cat), xs in spans.items():
        total, reach = 0.0, float("-inf")
        for a, b in sorted(xs):
            if b > reach:
                total += b - max(a, reach)
                reach = b
        out[line][cat] = total
    return {k: {c: round(v) for c, v in d.items()} for k, d in out.items()}


def _main_at(active: list[dict[str, Any]], rules: Rules) -> dict[str, Any] | None:
    anchors = [s for s in active if s["line"] == ANCHOR]
    if anchors:
        return min(anchors, key=lambda s: (rules.anchors[s["category"]].rank if s["category"] in rules.anchors else 9,
                                           -s["start"]))
    acts = [s for s in active if s["line"] != ANCHOR]
    if acts:
        return min(acts, key=lambda s: (s["tier"] if s["tier"] is not None else 9, -s["start"]))
    return None


def public_label(s: dict[str, Any], rules: Rules) -> str:
    if s["category"] == "class" and s.get("course"):
        return s["course"]
    if s["category"] in ("game", "custom", "out") and s.get("detail"):
        return s["detail"]
    return rules.short(s["category"])


def timeline(segs: list[dict[str, Any]], rules: Rules) -> list[dict[str, Any]]:
    """把两条线按显示规则（锚定优先，其次等级最高的活动）推导成一行“主状态”色块。"""
    points = sorted({p for s in segs for p in (s["start"], s["end"])})
    blocks: list[dict[str, Any]] = []
    for a, b in zip(points, points[1:]):
        active = [s for s in segs if s["start"] <= a and s["end"] >= b]
        main = _main_at(active, rules)
        if main is None:
            continue
        label = public_label(main, rules)
        last = blocks[-1] if blocks else None
        if last and last["end"] == a and last["category"] == main["category"] and last["label"] == label:
            last["end"] = b
        else:
            blocks.append({"start": a, "end": b, "category": main["category"], "label": label})
    return blocks


def today(db: DB, rules: Rules, now: float, tz: ZoneInfo, start_hour: int) -> dict[str, Any]:
    a, b, date = day_bounds(now, tz, start_hour)
    segs = _clip(db.segments_between(a, b), a, b, now)
    return {
        "date": date,
        "start": a,
        "end": b,
        "timeline": timeline(segs, rules),
        "totals": totals(segs),
        "updated_at": now,
    }
