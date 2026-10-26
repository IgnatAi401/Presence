"""上报信号的统一入口：把各设备、各版本的写法翻译成引擎认识的一种。

已经装在手机上的快捷指令、旧版 Mac 小工具都不用改，旧写法在这里翻译：
- manual 的结束写法 wake / stop_eat / leave_office / stop_out / stop_custom / cancel_class
  → {"state": 被结束的状态, "action": "stop"}；clear → {"action": "stop"}（结束全部手动状态）
- 私有标记 hidden → private
新设备直接发 {"state": ..., "action": "start" | "stop"} 和 private 即可。
"""

from __future__ import annotations

from typing import Any

# 上报类型 → 活动类别（desktop 按 rules.yaml 分类，manual 按 state，heartbeat 只续期）
FEED_CATEGORY = {"music": "music", "video": "video", "game": "game", "phone_app": "phone"}

# 旧的结束写法 → 被结束的状态
LEGACY_STOP = {
    "wake": "sleep",
    "stop_eat": "eat",
    "leave_office": "office",
    "stop_out": "out",
    "stop_custom": "custom",
    "cancel_class": "class",
}


def normalize(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """返回翻译后的 data（不改原对象）。"""
    if data.get("hidden"):
        data = {k: v for k, v in data.items() if k != "hidden"} | {"private": True}
    if kind == "manual":
        state = data.get("state")
        if state in LEGACY_STOP:
            data = {**data, "state": LEGACY_STOP[state], "action": "stop"}
        elif state == "clear":
            data = {k: v for k, v in data.items() if k != "state"} | {"action": "stop"}
        elif "action" not in data:
            data = {**data, "action": "start"}
    return data
