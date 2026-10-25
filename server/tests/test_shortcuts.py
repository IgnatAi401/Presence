"""用导出文件里的状态字典验证与服务器的契约，不访问真实服务。"""
import json
import plistlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presence.config import Settings
from presence.main import create_app
from presence.rules import Rules

SHORTCUTS = Path(__file__).resolve().parents[2] / "shortcuts" / "source"
RULES = Rules.load(Path(__file__).resolve().parents[1] / "config" / "rules.yaml")


def load(title):
    return plistlib.loads((SHORTCUTS / f"Presence · {title}.shortcut").read_bytes())


def parameters(title, identifier):
    return [a["WFWorkflowActionParameters"] for a in load(title)["WFWorkflowActions"]
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions." + identifier]


def payload(title, outputs):
    data = {}
    fields = parameters(title, "dictionary")[0]["WFItems"]["Value"]["WFDictionaryFieldValueItems"]
    for item in fields:
        key = item["WFKey"]["Value"]["string"]
        token = item["WFValue"]["Value"]
        if token.get("attachmentsByRange"):
            value = outputs.get("tier", 2) if key == "tier" else outputs[key]
        else:
            value = token["string"]
        data[key] = float(value) if item["WFItemType"] == 3 else value
    return report_payload(data)


def report_payload(data):
    # 按上报指令的实际参数连接构造请求；不能在测试中自行补上 data，
    # 否则会掩盖 iOS 导入后“未向 data 提供值”的字段序列化错误。
    reporter_outputs = {}
    routing_group = None
    routing_selected = False
    routing_enabled = True
    routing_output = None

    def resolve(value):
        if isinstance(value, str):
            return value
        kind, body = value["WFSerializationType"], value["Value"]
        if kind == "WFTextTokenString":
            if "attachmentsByRange" not in body:
                return body["string"]
            assert body["string"] == "\ufffc"
            body = body["attachmentsByRange"]["{0, 1}"]
        else:
            assert kind == "WFTextTokenAttachment"
        if body["Type"] == "ExtensionInput":
            return data
        return reporter_outputs[body["OutputUUID"]]

    for step in load("上报")["WFWorkflowActions"]:
        name, params = step["WFWorkflowActionIdentifier"], step["WFWorkflowActionParameters"]
        if name.endswith(".conditional"):
            if params.get("WFConditionalActionString") == "phone_app":
                routing_group = params["GroupingIdentifier"]
                routing_selected = resolve(params["WFInput"]["Variable"]) == "phone_app"
                routing_enabled = routing_selected
            elif params.get("GroupingIdentifier") == routing_group:
                if params["WFControlFlowMode"] == 1:
                    routing_enabled = not routing_selected
                elif params["WFControlFlowMode"] == 2:
                    reporter_outputs[params["UUID"]] = routing_output
                    routing_group = None
                    routing_enabled = True
            continue
        if routing_group and not routing_enabled:
            continue
        if name.endswith(".getvalueforkey") and params["WFInput"]["Value"]["Type"] == "ExtensionInput":
            reporter_outputs[params["UUID"]] = data.get(params["WFDictionaryKey"], "")
            continue
        if name.endswith(".dictionary"):
            result = {
                resolve(item["WFKey"]): (float(resolve(item["WFValue"])) if item["WFItemType"] == 3 else resolve(item["WFValue"]))
                for item in params["WFItems"]["Value"]["WFDictionaryFieldValueItems"]}
            if routing_group:
                routing_output = result
            else:
                reporter_outputs[params["UUID"]] = result
        elif name.endswith(".setvalueforkey"):
            assert params["WFDictionaryValue"]["WFSerializationType"] == "WFTextTokenString"
            base = dict(resolve(params["WFDictionary"]))
            base[resolve(params["WFDictionaryKey"])] = resolve(params["WFDictionaryValue"])
            reporter_outputs[params["UUID"]] = base
        elif name.endswith(".gettext") and params.get("UUID"):
            try:
                value = resolve(params["WFTextActionText"])
            except KeyError:
                continue  # 与请求正文无关的令牌文本
            reporter_outputs[params["UUID"]] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        elif name.endswith(".downloadurl"):
            assert params["WFHTTPBodyType"] == "File"
            return json.loads(resolve(params["WFRequestVariable"]))
    raise AssertionError("上报指令缺少请求动作")


@pytest.fixture
def phone(tmp_path):
    now = [1_800_000_000.0]
    settings = Settings(devices={"iphone": "test-iphone"}, db_path=tmp_path / "p.db")
    with TestClient(create_app(settings, RULES, clock=lambda: now[0], background=False)) as client:
        def send(title, **outputs):
            signal = report_payload(title) if isinstance(title, dict) else payload(title, outputs)
            result = client.post("/presence/api/signal", json=signal,
                                 headers={"Authorization": "Bearer test-iphone"})
            assert result.status_code == 200
            assert result.json() == {"ok": True, "accepted": 1}
            now[0] += 1
            return client.get("/presence/api/now").json()
        yield send, client, now


def categories(snapshot):
    return {snapshot["main"]["category"], *(item["category"] for item in snapshot["side"])}


def test_location_and_manual_transitions(phone):
    send, client, _ = phone
    assert send("到办公室")["main"]["category"] == "office"
    assert client.get("/presence/api/today").json()["totals"]["activity"].get("research", 0) == 0
    eating = send("吃饭")
    assert eating["main"]["category"] == "eat"
    assert "office" in categories(eating)
    assert "eat" in categories(send("起床"))
    assert "office" not in categories(send("离开办公室"))
    sleeping = send("睡觉")
    assert sleeping["main"]["category"] == "sleep"
    assert "eat" not in categories(sleeping)
    assert "sleep" not in categories(send("起床"))


def test_sleep_ends_office_and_custom_survives_wake(phone):
    send, _, _ = phone
    send("到办公室")
    assert "office" not in categories(send("睡觉"))
    message = '写论文："摩擦模型"\n喝茶 🍵 \\ simulation'
    assert send("自定义状态", text=message, minutes=5)["main"]["category"] == "sleep"
    awake = send("起床")
    assert awake["main"]["category"] == "custom"
    assert awake["main"]["label"] == message
    assert awake["main"]["tier"] == 2


def test_food_expires_to_office_and_custom_expires(phone):
    send, client, now = phone
    send("到办公室")
    send("吃饭")
    now[0] += 3601
    client.app.state.presence.update(now[0])  # 代替本测试中关闭的后台定时器
    assert client.get("/presence/api/now").json()["main"]["category"] == "office"
    send("离开办公室")
    assert send("自定义状态", text="看书", minutes=1)["main"]["category"] == "custom"
    now[0] += 61
    client.app.state.presence.update(now[0])
    assert "custom" not in categories(client.get("/presence/api/now").json())


def test_stop_eating_preserves_office_and_custom(phone):
    send, _, _ = phone
    send("到办公室")
    send("自定义状态", text="看书", minutes=120)
    send("吃饭")
    stopped = send("停止吃饭")
    assert stopped["main"]["category"] == "office"
    assert categories(stopped) == {"office", "custom"}
    assert categories(send("停止吃饭")) == {"office", "custom"}


def test_stop_eating_does_not_end_sleep(phone):
    send, _, _ = phone
    send("睡觉")
    assert send("停止吃饭")["main"]["category"] == "sleep"


@pytest.mark.parametrize("description", ["探索老城区", "街头摄影", "到处逛逛", '  拍“街头”\n夜景 📷  ', ""])
def test_outing_shortcuts_and_saved_description(phone, description):
    send, client, now = phone
    send("到办公室")
    started = send("出去玩", text=description)
    expected = description.strip()
    assert started["main"]["label"] == (f"出去玩 · {expected}" if expected else "出去玩")
    assert "office" not in categories(started)
    now[0] += 7200
    send("吃饭")
    assert "out" in categories(client.get("/presence/api/now").json())
    assert categories(send("结束出去玩")) == {"eat"}
    assert categories(send("结束出去玩")) == {"eat"}
    db = client.app.state.presence.db
    row = db.conn.execute("SELECT * FROM segments WHERE category = 'out'").fetchone()
    assert row["detail"] == (expected or None)
    assert row["end"] - row["start"] == 7202
    saved = db.conn.execute("SELECT * FROM manual_states WHERE state = 'out'").fetchone()
    assert saved["ended_at"] == row["end"]


def test_outing_export_has_optional_safe_text():
    prompt = parameters("出去玩", "ask")[0]
    assert prompt["WFInputType"] == "Text"
    assert prompt["WFAskActionDefaultAnswer"] == "到处逛逛"
    assert parameters("出去玩", "text.replace")[0]["WFInput"]["WFSerializationType"] == "WFTextTokenString"
    assert payload("出去玩", {"text": '街头摄影 "夜景"\n📷'})["data"]["text"] == '街头摄影 "夜景"\n📷'


def test_exports_use_shared_reporter_and_safe_json():
    files = list(SHORTCUTS.glob("*.shortcut"))
    assert len(files) == 11
    for title in ("睡觉", "起床", "吃饭", "停止吃饭", "到办公室", "离开办公室", "出去玩", "结束出去玩", "自定义状态", "退出自定义状态"):
        run = parameters(title, "runworkflow")[0]
        assert run["WFWorkflowName"] == "Presence · 上报"
        assert run["WFShowWorkflow"] is False
        assert not parameters(title, "downloadurl")
    report = load("上报")
    question = report["WFWorkflowImportQuestions"][0]
    target = report["WFWorkflowActions"][question["ActionIndex"]]["WFWorkflowActionParameters"]
    assert target[question["ParameterKey"]] == "__IPHONE_DEVICE_TOKEN__"
    request = parameters("上报", "downloadurl")[0]
    assert request["WFHTTPMethod"] == "POST" and request["WFHTTPBodyType"] == "File"
    assert "WFJSONValues" not in request  # 整个字典的变量在 JSON 编辑器导入后会变成 0 项
    assert request["WFRequestVariable"]["WFSerializationType"] == "WFTextTokenAttachment"
    headers = {item["WFKey"]["Value"]["string"]: item["WFValue"]["Value"]["string"]
               for item in request["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"]}
    assert headers["Content-Type"] == "application/json"
    envelope = parameters("上报", "setvalueforkey")[0]
    assert envelope["WFDictionaryKey"] == "data"
    assert "WFInput" not in envelope
    assert envelope["WFDictionary"]["WFSerializationType"] == "WFTextTokenAttachment"
    assert envelope["WFDictionaryValue"]["WFSerializationType"] == "WFTextTokenString"
    assert envelope["WFDictionaryValue"]["Value"]["attachmentsByRange"]["{0, 1}"]["Type"] == "ExtensionInput"
    assert "WFInput" not in request
    assert request["WFURL"]["WFSerializationType"] == "WFTextTokenString"
    # 替换文本的输入是文本字段，裸 Attachment 在导入后会丢失用户输入。
    trim = parameters("自定义状态", "text.replace")[0]
    assert trim["WFInput"]["WFSerializationType"] == "WFTextTokenString"
    count = parameters("自定义状态", "count")[0]
    assert count["WFCountType"] == "Characters"
    assert "Input" in count
    duration = parameters("自定义状态", "ask")[1]
    assert duration["WFAskActionDefaultAnswerNumber"] == 120
    priority = parameters("自定义状态", "ask")[2]
    assert priority["WFInputType"] == "Number" and priority["WFAskActionDefaultAnswerNumber"] == 2
    assert priority["WFAskActionAllowsDecimalNumbers"] is False
    # 检查魔术变量均引用本文件中此前的动作，避免未绑定输入。
    def references(value):
        if isinstance(value, dict):
            if value.get("Type") == "ActionOutput":
                yield value["OutputUUID"]
            for child in value.values():
                yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)
    for file in files:
        seen = set()
        for step in plistlib.loads(file.read_bytes())["WFWorkflowActions"]:
            params = step["WFWorkflowActionParameters"]
            assert set(references(params)) <= seen
            if "UUID" in params:
                seen.add(params["UUID"])


PHONE_LABELS = {
    'X': '在 X 上刷动态', 'Instagram': '在 IG 看看大家的日常', '小黑盒': '在刷小黑盒',
    '小红书': '在小红书翻翻生活灵感',
    '抖音': '在抖音刷短视频', 'TikTok': '在 TikTok 刷短视频', 'Reddit': '在 Reddit 逛社区',
    'Apple Music': '在 Apple Music 上听音乐', 'Discord': '在 Discord 里聊天',
    'Safari': '在用 Safari 逛网页', 'B站': '在刷 B站', 'YouTube': '在 YouTube 看视频',
    'Telegram': '在 Telegram 看消息、聊聊天', 'WhatsApp': '在 WhatsApp 和朋友聊聊天',
    'Facebook': '在 Facebook 看看新动态',
    'ChatGPT': '在和 ChatGPT 聊天', 'DeepSeek': '在和 DeepSeek 聊天', 'Claude': '在和 Claude 聊天',
    '淘宝': '在淘宝淘点好东西', '京东': '在京东逛逛', '闲鱼': '在闲鱼淘二手',
}


@pytest.mark.parametrize('app,label', PHONE_LABELS.items())
def test_each_phone_pair_and_public_copy(phone, app, label):
    send, client, now = phone
    data = {'kind': 'phone_app', 'app': app, 'event': 'open'}
    signal = report_payload(data)
    assert signal['kind'] == 'phone_app' and signal['ttl'] == 1800
    assert signal['data']['event'] == 'open' and signal['data']['app'] == app
    opened = send(data)
    assert opened['main']['category'] == 'phone' and opened['main']['label'] == label
    assert opened['main']['app'] == app  # 主状态的图标必须能通过原始 App 名精确匹配
    now[0] += 10
    assert 'phone' not in categories(send({**data, 'event': 'close'}))
    assert client.get('/presence/api/today').json()['totals']['activity']['phone'] == 11


def test_phone_route_does_not_replace_custom_and_wrong_close_is_safe(phone):
    send, _, _ = phone
    send('自定义状态', text='看书', minutes=120)
    send({'kind': 'phone_app', 'app': 'X', 'event': 'open'})
    switched = send({'kind': 'phone_app', 'app': 'B站', 'event': 'open'})
    assert categories(switched) == {'custom', 'phone'}
    late = send({'kind': 'phone_app', 'app': 'X', 'event': 'close'})
    assert [item['label'] for item in late['side']] == ['在刷 B站']
    assert late['side'][0]['app'] == 'B站'  # 在伴随状态中同样提供图标标识
    assert categories(send({'kind': 'phone_app', 'app': 'B站', 'event': 'close'})) == {'custom'}


@pytest.mark.parametrize('tier', [1, 2, 3, 4, 7])
def test_custom_priority_and_exit_export_contract(phone, tier):
    send, _, _ = phone
    send('到办公室')
    s = send('自定义状态', text='写作', minutes=120, tier=tier)
    custom = next(i for i in [s['main'], *s['side']] if i['category'] == 'custom')
    assert custom['type'] == ('anchor' if tier == 1 else 'activity')
    if tier != 1:
        assert custom['tier'] == tier
    assert categories(send('退出自定义状态')) == {'office'}
    assert categories(send('退出自定义状态')) == {'office'}
