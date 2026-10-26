"""统计查询：一天的边界、公开时间线（由两条线推导主状态）、各类别时长、历史与私有回看。

公开查询去掉私有片段；私有查询（private_*）保留全部细节。
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .db import DB
from .engine import ACTIVITY, ANCHOR
from .rules import Rules

MAX_DAYS = 400  # 一次最多查多少天
TOP_N = 10
MEDIA_GROUPS = {"game": "game", "track": "music", "video": "video", "app": "app"}
GROUPS = ("category", "project", "site", *MEDIA_GROUPS)


def day_bounds(now: float, tz: ZoneInfo, start_hour: int) -> tuple[float, float, str]:
    """返回 (当天开始, 当天结束, 日期字符串)；凌晨 start_hour 点之前仍算前一天。"""
    local = datetime.fromtimestamp(now, tz)
    day = (local - timedelta(hours=start_hour)).date()
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp(), day.isoformat()


def parse_date(value: str) -> Date:
    try:
        return Date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"日期应写成 YYYY-MM-DD：{value!r}") from None


def date_bounds(day: Date, tz: ZoneInfo, start_hour: int) -> tuple[float, float]:
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), start.timetz())
    return start.timestamp(), end.timestamp()


def date_range(frm: str | None, to: str | None, now: float, tz: ZoneInfo, start_hour: int,
               default_days: int) -> list[Date]:
    """[frm, to] 内的每一天（含两端）；不写 to 就到今天，不写 frm 就往前 default_days 天。"""
    end = parse_date(to) if to else Date.fromisoformat(day_bounds(now, tz, start_hour)[2])
    start = parse_date(frm) if frm else end - timedelta(days=default_days - 1)
    n = (end - start).days + 1
    if n < 1:
        raise ValueError("开始日期不能晚于结束日期")
    if n > MAX_DAYS:
        raise ValueError(f"一次最多查 {MAX_DAYS} 天")
    return [start + timedelta(days=i) for i in range(n)]


def _clip(rows, a: float, b: float, now: float) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        s, e = max(r["start"], a), min(r["end"] if r["end"] is not None else now, b, now)
        if e > s:
            out.append({**dict(r), "start": s, "end": e})
    return out


def _union(spans) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _length(spans) -> float:
    return sum(b - a for a, b in _union(spans))


def _within(rows: list[dict[str, Any]], spans: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """只留 rows 落在 spans 里的部分。spans 须已合并、排好序。"""
    out = []
    for r in rows:
        for a, b in spans:
            s, e = max(r["start"], a), min(r["end"], b)
            if e > s:
                out.append({**r, "start": s, "end": e})
    return out


def _is_private(s: dict[str, Any]) -> bool:
    return bool(json.loads(s.get("meta_json") or "{}").get("private"))


def public_segments(db: DB, a: float, b: float, now: float) -> list[dict[str, Any]]:
    """公开统计用的片段：去掉私有片段。"""
    return [s for s in _clip(db.segments_between(a, b), a, b, now) if not _is_private(s)]


def serious_spans(segs: list[dict[str, Any]], rules: Rules) -> list[tuple[float, float]]:
    """正事（科研、开发）实际在做的时段，不含摸鱼；几种正事重叠只算一次。"""
    return _union((s["start"], s["end"]) for s in segs
                  if s["line"] == ACTIVITY and rules.is_serious(s["category"]) and not s["slacking"])


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
    if s["category"] == "out" and s.get("detail"):
        return f"出去玩 · {s['detail']}"
    if s["category"] in ("game", "custom") and s.get("detail"):
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


def names(rules: Rules, cats) -> dict[str, str]:
    """各类别的简称。"""
    return {c: rules.short(c) for c in cats}


def today(db: DB, rules: Rules, now: float, tz: ZoneInfo, start_hour: int, day: Date | None = None) -> dict[str, Any]:
    """某一天（默认今天）的公开时间线和各类别时长。"""
    if day is None:
        day = Date.fromisoformat(day_bounds(now, tz, start_hour)[2])
    a, b = date_bounds(day, tz, start_hour)
    segs = public_segments(db, a, b, now)
    t = totals(segs)
    return {
        "date": day.isoformat(),
        "start": a,
        "end": b,
        "timeline": timeline(segs, rules),
        "totals": t,
        "names": names(rules, [c for line in t.values() for c in line]),
        "updated_at": now,
    }


def _media(db: DB, types: list[str], a: float, b: float, now: float, private: bool) -> list[dict[str, Any]]:
    return _clip(db.media_rows(types, a, b, include_private=private), a, b, now)


def media_rank(rows: list[dict[str, Any]], limit: int | None = TOP_N) -> list[dict[str, Any]]:
    """按内容汇总时长和播放次数（暂停后继续的算同一次），从多到少。"""
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    plays: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        k = (r["type"], r["key"])
        it = acc.setdefault(k, {"key": r["key"], "title": None, "artist": None, "seconds": 0.0})
        it["title"] = it["title"] or r["title"]
        it["artist"] = it["artist"] or r["artist_or_owner"]
        it["seconds"] += r["end"] - r["start"]
        plays[k].add(r["play"])
    out = sorted(acc.values(), key=lambda x: -x["seconds"])
    for k, it in acc.items():
        it["seconds"] = round(it["seconds"])
        it["plays"] = len(plays[k])
    return out[:limit] if limit else out


def history(db: DB, rules: Rules, days: list[Date], now: float, tz: ZoneInfo, start_hour: int) -> dict[str, Any]:
    """公开历史：每天各类别时长和正事时长，区间内的游戏榜、听歌榜。不含项目名、网站和视频。"""
    a, b = date_bounds(days[0], tz, start_hour)[0], date_bounds(days[-1], tz, start_hour)[1]
    segs = public_segments(db, a, b, now)
    out_days, cats = [], set()
    for d in days:
        da, db_ = date_bounds(d, tz, start_hour)
        if da >= now:
            break
        part = _clip(segs, da, db_, now)
        t = totals(part)
        cats.update(c for line in t.values() for c in line)
        out_days.append({"date": d.isoformat(), "totals": t, "serious_sec": round(_length(serious_spans(part, rules)))})
    media = _media(db, ["game", "music"], a, b, now, private=False)
    return {
        "from": days[0].isoformat(),
        "to": days[-1].isoformat(),
        "days": out_days,
        "games": media_rank([m for m in media if m["type"] == "game"]),
        "music": media_rank([m for m in media if m["type"] == "music"]),
        "names": names(rules, cats),
        "updated_at": now,
    }


# -- 私有（只经过登录的 /private/* 接口调用）


def _segment_out(s: dict[str, Any]) -> dict[str, Any]:
    out = {k: s[k] for k in ("id", "line", "category", "start", "end", "tier", "detail", "project", "course",
                             "domain", "source") if s.get(k) is not None}
    out["slacking"] = bool(s["slacking"])
    if _is_private(s):
        out["private"] = True
    return out


def private_day(db: DB, rules: Rules, day: Date, now: float, tz: ZoneInfo, start_hour: int) -> dict[str, Any]:
    """某天的完整记录：所有线的片段（含项目名、网站）、播放记录、浏览记录。"""
    a, b = date_bounds(day, tz, start_hour)
    segs = _clip(db.segments_between(a, b), a, b, now)
    t = totals(segs)
    media = []
    for type_ in ("video", "music", "game", "app"):
        for r in _clip(db.media_history(type_, a, b), a, b, now):
            media.append({"type": type_, **{k: r[k] for k in ("key", "title", "artist", "album", "player", "page",
                                                              "source", "start", "end") if r[k] is not None},
                          "seconds": round(r["end"] - r["start"]), "private": bool(r["private"])})
    web = [{k: r[k] for k in ("domain", "title", "source", "start", "end") if r[k] is not None} | {"private": bool(r["private"])}
           for r in _clip(db.web_history(a, b), a, b, now)]
    return {
        "date": day.isoformat(),
        "start": a,
        "end": b,
        "segments": [_segment_out(s) for s in sorted(segs, key=lambda s: (s["start"], s["end"]))],
        "timeline": timeline(segs, rules),
        "totals": t,
        "media": sorted(media, key=lambda m: m["start"]),
        "web": sorted(web, key=lambda w: w["start"]),
        "names": names(rules, [*rules.anchors, *rules.activities]),
        "updated_at": now,
    }


def _during(db: DB, category: str, a: float, b: float, now: float) -> list[tuple[float, float]]:
    """某个锚定状态（上课、吃饭……）的时段。"""
    return _union((s["start"], s["end"]) for s in _clip(db.segments_between(a, b), a, b, now)
                  if s["line"] == ANCHOR and s["category"] == category)


def summary(db: DB, rules: Rules, days: list[Date], group: str, now: float, tz: ZoneInfo, start_hour: int,
            during: str | None = None) -> dict[str, Any]:
    """区间汇总，按类别 / 项目 / 网站 / 游戏 / 歌 / 视频 / 手机 App 排行。
    during 写锚定状态（例如 class）时只统计那段时间里的，回答“课上在干什么”。"""
    if group not in GROUPS:
        raise ValueError(f"group 只能是 {' / '.join(GROUPS)}")
    if during is not None and during not in rules.anchors:
        raise ValueError(f"during 只能是锚定状态：{' / '.join(rules.anchors)}")
    a, b = date_bounds(days[0], tz, start_hour)[0], date_bounds(days[-1], tz, start_hour)[1]
    spans = _during(db, during, a, b, now) if during else None

    def keep(rows):
        return _within(rows, spans) if spans is not None else rows

    items: list[dict[str, Any]]
    if group in MEDIA_GROUPS:
        items = media_rank(keep(_media(db, [MEDIA_GROUPS[group]], a, b, now, private=True)), limit=None)
    elif group == "site":
        rows = keep(_clip(db.web_history(a, b), a, b, now))
        acc: dict[str, list] = defaultdict(list)
        for r in rows:
            acc[r["domain"]].append((r["start"], r["end"]))
        items = [{"key": k, "title": rules.site_names.get(k.removeprefix("www."), k.removeprefix("www.")),
                  "seconds": round(_length(v)), "visits": len(v)} for k, v in acc.items()]
    else:
        segs = keep([s for s in _clip(db.segments_between(a, b), a, b, now) if not s["slacking"]])
        if during:
            segs = [s for s in segs if s["line"] == ACTIVITY]  # 课上在干什么：只看活动线
        acc = defaultdict(list)
        for s in segs:
            k = s["category"] if group == "category" else s["project"]
            if k:
                acc[(s["line"], k) if group == "category" else ("", k)].append((s["start"], s["end"]))
        items = [{"key": k, "line": line or None, "title": rules.short(k) if group == "category" else k,
                  "seconds": round(_length(v))} for (line, k), v in acc.items()]
        if group == "project":
            for it in items:
                it.pop("line")
    items = sorted((i for i in items if i["seconds"] > 0), key=lambda i: -i["seconds"])
    return {"from": days[0].isoformat(), "to": days[-1].isoformat(), "group": group, "during": during,
            "items": items, "updated_at": now}


def _channel(s: dict[str, Any], rules: Rules) -> tuple[str, str]:
    if s.get("domain"):
        d = s["domain"].removeprefix("www.")
        return s["category"], rules.site_names.get(d, d)
    if s["category"] in ("game", "custom", "out") and s.get("detail"):
        return s["category"], s["detail"]
    return s["category"], rules.short(s["category"])


def focus(db: DB, rules: Rules, days: list[Date], now: float, tz: ZoneInfo, start_hour: int) -> dict[str, Any]:
    """专注度：每天正事时长、最长连续专注、被打断（摸鱼）次数和时长；区间内主要的摸鱼渠道。"""
    out_days = []
    channels: dict[tuple[str, str], list] = defaultdict(list)
    for d in days:
        a, b = date_bounds(d, tz, start_hour)
        if a >= now:
            break
        segs = _clip(db.segments_between(a, b), a, b, now)
        focus_spans = serious_spans(segs, rules)
        slack = [s for s in segs if s["slacking"] and rules.is_serious(s["category"])]
        slack_spans = _union((s["start"], s["end"]) for s in slack)
        best = max(focus_spans, key=lambda x: x[1] - x[0], default=None)
        out_days.append({
            "date": d.isoformat(),
            "focus_sec": round(_length(focus_spans)),
            "longest_sec": round(best[1] - best[0]) if best else 0,
            "longest_start": best[0] if best else None,
            "interruptions": sum(1 for s in slack if s["start"] >= a),
            "slack_sec": round(_length(slack_spans)),
        })
        # 摸鱼时段里实际在做的事（听歌一直在后台，不算渠道）
        others = [s for s in segs if s["line"] == ACTIVITY and not rules.is_serious(s["category"]) and s["category"] != "music"]
        for s in _within(others, slack_spans):
            channels[_channel(s, rules)].append((s["start"], s["end"]))
    ranked = sorted(({"category": c, "title": t, "seconds": round(_length(v))} for (c, t), v in channels.items()),
                    key=lambda x: -x["seconds"])
    return {"from": days[0].isoformat(), "to": days[-1].isoformat(), "days": out_days,
            "channels": [c for c in ranked if c["seconds"] > 0][:TOP_N], "updated_at": now}


EXPORT_FIELDS = ("id", "line", "category", "start", "end", "start_local", "end_local", "seconds", "tier", "detail",
                 "project", "course", "domain", "source", "slacking", "private")


def export(db: DB, days: list[Date], fmt: str, now: float, tz: ZoneInfo, start_hour: int) -> tuple[str, str]:
    """导出区间内的全部记录；返回 (内容, MIME 类型)。CSV 只含片段，JSON 另含播放和浏览记录。"""
    a, b = date_bounds(days[0], tz, start_hour)[0], date_bounds(days[-1], tz, start_hour)[1]
    segs = [_segment_out(s) for s in sorted(_clip(db.segments_between(a, b), a, b, now), key=lambda s: s["start"])]
    def local(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")

    for s in segs:
        s.update(start_local=local(s["start"]), end_local=local(s["end"]), seconds=round(s["end"] - s["start"]))
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(segs)
        return "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8"  # 带 BOM，Excel 打开中文不乱码
    if fmt != "json":
        raise ValueError("format 只能是 csv 或 json")
    media = [{k: r[k] for k in ("type", "key", "title", "artist_or_owner", "album", "player", "page", "play",
                                "start", "end", "source", "private")} for r in _clip(db.media_rows(
                                    ["game", "music", "video", "app"], a, b, include_private=True), a, b, now)]
    web = [dict(r) for r in _clip(db.web_history(a, b), a, b, now)]
    data = {"from": days[0].isoformat(), "to": days[-1].isoformat(), "timezone": str(tz), "segments": segs,
            "media": sorted(media, key=lambda m: m["start"]), "web": sorted(web, key=lambda w: w["start"])}
    return json.dumps(data, ensure_ascii=False, indent=1), "application/json; charset=utf-8"
