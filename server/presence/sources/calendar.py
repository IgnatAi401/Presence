"""从苹果日历（EventKit）读取课程表。

第一次运行时 macOS 会弹窗请求日历权限（授予启动服务的终端 App），同意一次即可。
单独测试：uv run python -m presence.sources.calendar 课程表
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time

from ..engine import Course

log = logging.getLogger(__name__)

DAYS_AHEAD = 14
DAYS_BACK = 1


class CalendarUnavailable(RuntimeError):
    pass


def _store():
    try:
        import EventKit  # noqa: PLC0415  只在 macOS 上可用
    except ImportError as exc:
        raise CalendarUnavailable("当前系统没有 EventKit（只能在 macOS 上读取日历）") from exc

    store = EventKit.EKEventStore.alloc().init()
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    full_access = getattr(EventKit, "EKAuthorizationStatusFullAccess", 3)
    if status == full_access:
        return EventKit, store
    if status not in (0,):  # 0 = 还没决定；其余是已拒绝 / 受限 / 只写
        raise CalendarUnavailable("没有日历的完整访问权限：请在“系统设置 → 隐私与安全性 → 日历”里允许终端")

    done = threading.Event()
    result = {}

    def callback(granted, error):
        result["granted"] = bool(granted)
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(callback)
    else:
        store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, callback)
    if not done.wait(120) or not result.get("granted"):
        raise CalendarUnavailable("日历权限请求被拒绝或超时")
    return EventKit, store


def fetch_courses(calendar_name: str, title_strip: str | None = None, now: float | None = None) -> list[Course]:
    """读取名为 calendar_name 的日历中，过去 1 天到未来 14 天的非全天事件。"""
    from Foundation import NSDate  # noqa: PLC0415

    EventKit, store = _store()
    calendars = [c for c in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent) if c.title() == calendar_name]
    if not calendars:
        raise CalendarUnavailable(f"找不到名为“{calendar_name}”的日历")

    now = now or time.time()
    start = NSDate.dateWithTimeIntervalSince1970_(now - DAYS_BACK * 86400)
    end = NSDate.dateWithTimeIntervalSince1970_(now + DAYS_AHEAD * 86400)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(start, end, calendars)
    strip = re.compile(title_strip) if title_strip else None

    courses = []
    for ev in store.eventsMatchingPredicate_(predicate) or []:
        if ev.isAllDay():
            continue
        s = ev.startDate().timeIntervalSince1970()
        e = ev.endDate().timeIntervalSince1970()
        title = str(ev.title() or "").strip()
        if strip:
            title = strip.sub("", title).strip() or title
        # 重复事件的每一次共用同一个标识，所以拼上开始时间
        event_id = f"{ev.calendarItemExternalIdentifier() or ev.eventIdentifier()}@{int(s)}"
        location = str(ev.location()) if ev.location() else None
        courses.append(Course(event_id, title, s, e, location))
    return sorted(courses, key=lambda c: c.start)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "课程表"
    try:
        for c in fetch_courses(name):
            print(time.strftime("%m-%d %a %H:%M", time.localtime(c.start)),
                  time.strftime("%H:%M", time.localtime(c.end)), c.title, c.location or "")
    except CalendarUnavailable as exc:
        print("读取失败：", exc)
        sys.exit(1)
