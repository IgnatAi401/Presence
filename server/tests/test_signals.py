"""信号入口：旧写法翻译成统一写法，已装的快捷指令和旧版小工具不用改。"""

import pytest

from presence.signals import normalize


@pytest.mark.parametrize("old, state", [
    ("wake", "sleep"), ("stop_eat", "eat"), ("leave_office", "office"),
    ("stop_out", "out"), ("stop_custom", "custom"), ("cancel_class", "class"),
])
def test_legacy_stop_verbs(old, state):
    assert normalize("manual", {"state": old}) == {"state": state, "action": "stop"}


def test_clear_stops_everything():
    assert normalize("manual", {"state": "clear"}) == {"action": "stop"}


def test_start_keeps_fields_and_new_form_passes_through():
    assert normalize("manual", {"state": "out", "text": "公园"}) == {"state": "out", "text": "公园", "action": "start"}
    assert normalize("manual", {"state": "eat", "action": "stop"}) == {"state": "eat", "action": "stop"}


def test_hidden_becomes_private():
    data = {"domain": "a.com", "hidden": True}
    assert normalize("desktop", data) == {"domain": "a.com", "private": True}
    assert data == {"domain": "a.com", "hidden": True}  # 不改原对象
    assert normalize("desktop", {"domain": "a.com"}) == {"domain": "a.com"}
