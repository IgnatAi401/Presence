"""生成 iPhone 快捷指令；只用标准库，通过 server 的 uv 环境执行。"""
from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parent
REPORTER = "Presence · 上报"
TOKEN_PLACEHOLDER = "__IPHONE_DEVICE_TOKEN__"
ENDPOINT = "https://api.ignat.ai/presence/api/signal"


def uid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "presence-shortcuts/" + name)).upper()


def action(name: str, **params) -> dict:
    return {"WFWorkflowActionIdentifier": "is.workflow.actions." + name,
            "WFWorkflowActionParameters": params}


def ref(name: str | None = None) -> dict:
    value = {"Type": "ActionOutput", "OutputUUID": uid(name)} if name else {"Type": "ExtensionInput"}
    return {"Value": value, "WFSerializationType": "WFTextTokenAttachment"}


def text(value: str | dict, prefix: str = "") -> dict:
    if isinstance(value, str):
        body = {"string": prefix + value}
    else:
        # UTF-16 offset; our prefixes are ASCII.
        body = {"string": prefix + "\ufffc", "attachmentsByRange": {
            f"{{{len(prefix)}, 1}}": value["Value"]}}
    return {"Value": body, "WFSerializationType": "WFTextTokenString"}


def dictionary(values: dict) -> dict:
    items = []
    for key, value in values.items():
        numeric = isinstance(value, (int, float))
        encoded = (value if isinstance(value, dict) and value.get("WFSerializationType") == "WFTextTokenString"
                   else text(str(value) if numeric else value))
        items.append({"WFItemType": 3 if numeric else 0, "WFKey": text(key), "WFValue": encoded})
    return {"Value": {"WFDictionaryFieldValueItems": items},
            "WFSerializationType": "WFDictionaryFieldValue"}


def guard(name: str, value: dict, condition: int, message: str | dict, **comparison) -> list[dict]:
    """条件成立时停止；条件输入使用 Shortcuts 的 Variable 包装格式。"""
    group = uid(name + "/guard")
    return [action("conditional", WFControlFlowMode=0, GroupingIdentifier=group,
                   WFInput={"Type": "Variable", "Variable": value},
                   WFCondition=condition, **comparison),
            action("alert", WFAlertActionTitle="Presence", WFAlertActionMessage=message,
                   WFAlertActionCancelButtonShown=False),
            action("exit"),
            action("conditional", WFControlFlowMode=2, GroupingIdentifier=group)]


def workflow(name: str, actions: list[dict], *, reporter: bool = False) -> dict:
    return {"WFWorkflowName": name, "WFWorkflowClientVersion": "3036.0.4.2",
            "WFWorkflowMinimumClientVersion": 1106,
            "WFWorkflowMinimumClientVersionString": "1106",
            "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4274264319,
                               "WFWorkflowIconGlyphNumber": 61440},
            "WFWorkflowTypes": [], "WFQuickActionSurfaces": [],
            "WFWorkflowInputContentItemClasses": ["WFDictionaryContentItem"],
            "WFWorkflowOutputContentItemClasses": [],
            "WFWorkflowHasShortcutInputVariables": reporter,
            "WFWorkflowHasOutputFallback": False,
            "WFWorkflowImportQuestions": ([{
                "Category": "Parameter", "ActionIndex": 1, "ParameterKey": "WFTextActionText",
                "Text": "填写服务器 [devices] 中的 iphone 令牌（只填令牌，不加 Bearer）。",
                "DefaultValue": TOKEN_PLACEHOLDER}] if reporter else []),
            "WFWorkflowActions": actions}


def reporter_workflow() -> dict:
    actions = [action("comment", WFCommentActionText=
                      "配置一次 iphone 令牌，供所有 Presence 指令共用。输入为状态字典。成功静默；失败请看系统错误或下方提示。"),
               action("gettext", WFTextActionText=TOKEN_PLACEHOLDER, UUID=uid("token"))]
    actions += guard("token", ref("token"), 4, "请先在本指令顶部的文本里填写 iphone 设备令牌。",
                     WFConditionalActionString=TOKEN_PLACEHOLDER)
    actions += guard("input", ref(), 101, "请运行睡觉、起床、吃饭等状态指令；不要单独运行上报。")
    actions += [action("getvalueforkey", WFInput=ref(), WFDictionaryKey="kind",
                       WFGetDictionaryValueType="Value", UUID=uid("input-kind")),
                action("gettext", WFTextActionText=text(ref("input-kind")), UUID=uid("input-kind-text")),
                action("conditional", WFControlFlowMode=0, GroupingIdentifier=uid("envelope-group"),
                       WFInput={"Type": "Variable", "Variable": ref("input-kind-text")},
                       WFCondition=4, WFConditionalActionString="phone_app"),
                action("dictionary", WFItems=dictionary({"kind": "phone_app", "ttl": 1800})),
                action("conditional", WFControlFlowMode=1, GroupingIdentifier=uid("envelope-group")),
                action("dictionary", WFItems=dictionary({"kind": "manual"})),
                action("conditional", WFControlFlowMode=2, GroupingIdentifier=uid("envelope-group"),
                       UUID=uid("envelope")),
                # Value 是文本参数，须用 WFTextTokenString 携带原生字典变量。
                # WFTextTokenAttachment 在此字段会被 iOS 当作没有填写值。
                action("setvalueforkey", WFDictionary=ref("envelope"), WFDictionaryKey="data",
                       WFDictionaryValue=text(ref()), UUID=uid("payload")),
                # JSON 字典编辑器不会保留整个字典的 Attachment（导入后变成 0 项）。
                # 先让系统把原生字典序列化为 JSON，再按文件正文发送。
                action("gettext", WFTextActionText=text(ref("payload")), UUID=uid("json-body")),
                action("url", WFURLActionURL=ENDPOINT, UUID=uid("endpoint")),
                action("downloadurl", WFURL=text(ref("endpoint")), Advanced=True, ShowHeaders=True,
                       WFHTTPMethod="POST", WFHTTPBodyType="File", WFRequestVariable=ref("json-body"),
                       WFHTTPHeaders=dictionary({"Authorization": text(ref("token"), "Bearer "),
                                                 "Content-Type": "application/json"}),
                       UUID=uid("response")),
                action("getvalueforkey", WFInput=ref("response"), WFDictionaryKey="accepted",
                       WFGetDictionaryValueType="Value", UUID=uid("accepted")),
                action("gettext", WFTextActionText=text(ref("accepted")), UUID=uid("accepted-text"))]
    # 先判断存在，再比较数值；错误 JSON 缺少 accepted 时也不能当成成功。
    actions += guard("missing-accepted", ref("accepted-text"), 101,
                     text(ref("response"), "上报失败，服务器响应：\n"))
    actions += [action("conditional", WFControlFlowMode=0, GroupingIdentifier=uid("accepted-group"),
                       WFInput={"Type": "Variable", "Variable": ref("accepted-text")},
                       WFCondition=4, WFConditionalActionString="1"),
                action("nothing"),
                action("conditional", WFControlFlowMode=1, GroupingIdentifier=uid("accepted-group")),
                action("alert", WFAlertActionTitle="Presence 上报失败",
                       WFAlertActionMessage="服务器没有接收这次状态。请检查服务器后重新运行。",
                       WFAlertActionCancelButtonShown=False),
                action("conditional", WFControlFlowMode=2, GroupingIdentifier=uid("accepted-group"))]
    return workflow(REPORTER, actions, reporter=True)


def state_workflow(title: str, state: str, comment: str) -> dict:
    name = "Presence · " + title
    actions = [action("comment", WFCommentActionText=comment)]
    values = {"state": state}
    if state == "custom":
        actions += [action("ask", WFAskActionPrompt="现在在做什么？这段文字会公开显示。",
                           WFInputType="Text", UUID=uid("custom-text")),
                    action("text.replace", WFInput=text(ref("custom-text")), WFReplaceTextFind=r"^\s+|\s+$",
                           WFReplaceTextReplace="", WFReplaceTextRegularExpression=True,
                           UUID=uid("custom-trimmed")),
                    action("count", Input=ref("custom-trimmed"), WFCountType="Characters",
                           UUID=uid("custom-length"))]
        # 空字符串也可能被 Shortcuts 当成“有值”的内容项；比较字符数才可靠。
        actions += guard("empty-custom", ref("custom-length"), 4, "状态文字不能为空。", WFNumberValue=0)
        actions += [action("ask", WFAskActionPrompt="持续多少分钟？（1～1440）",
                           WFInputType="Number", WFAskActionDefaultAnswerNumber=120,
                           WFAskActionAllowsDecimalNumbers=False, UUID=uid("custom-minutes"))]
        actions += guard("missing-minutes", ref("custom-minutes"), 101, "请输入有效的持续分钟数。")
        actions += guard("short-minutes", ref("custom-minutes"), 0, "持续时间至少为 1 分钟。", WFNumberValue=1)
        actions += guard("long-minutes", ref("custom-minutes"), 2, "持续时间最多为 1440 分钟。", WFNumberValue=1440)
        values.update(text=ref("custom-trimmed"), minutes=ref("custom-minutes"), tier=2)
    elif state == "eat":
        values["minutes"] = 60
    # Number 类型字段的动态变量也保留为 Number，避免把分钟数写成文字。
    fields = dictionary(values)
    if state == "custom":
        next(item for item in fields["Value"]["WFDictionaryFieldValueItems"]
             if item["WFKey"]["Value"]["string"] == "minutes")["WFItemType"] = 3
    actions += [action("dictionary", WFItems=fields, UUID=uid(state + "/data")),
                action("runworkflow", WFWorkflowName=REPORTER,
                       WFInput=ref(state + "/data"), WFShowWorkflow=False)]
    return workflow(name, actions)


def workflows() -> dict[str, dict]:
    result = {REPORTER: reporter_workflow()}
    for title, state, comment in [
        ("睡觉", "sleep", "手动点按。开始睡觉，结束吃饭和办公室锚定；最长 14 小时。"),
        ("起床", "wake", "手动点按。只结束睡觉，不清除其他状态。"),
        ("吃饭", "eat", "用于到达食堂的地点自动化，也可手动点按。持续 60 分钟，办公室锚定保留。"),
        ("停止吃饭", "stop_eat", "手动点按。只结束吃饭，保留办公室、睡觉、自定义等其他状态。需要服务器支持 stop_eat。"),
        ("到办公室", "office", "用于到达办公室的地点自动化。开启在办公室搞科研锚定，最长 12 小时。"),
        ("离开办公室", "leave_office", "用于离开办公室的地点自动化。只结束办公室锚定。"),
        ("自定义状态", "custom", "手动输入公开状态和持续分钟数。默认等级 2；锚定状态仍优先。"),
    ]:
        w = state_workflow(title, state, comment)
        result[w["WFWorkflowName"]] = w
    return result


def automation_workflows() -> dict[str, dict]:
    """OS 27 原生 App 自动化；空 App 选择器等待用户在 iPhone 上配置。"""
    apps = json.loads((ROOT / "automations" / "apps.json").read_text())
    result = {}
    for app in apps:
        for event, verb in (("open", "打开"), ("close", "退出")):
            name = f"Presence · {app['name']} · {verb}"
            key = f"automation/{app['app']}/{event}"
            actions = [
                action("comment", WFCommentActionText=f"在上方 App 触发器选择 {app['name']}，然后启用自动运行。下方上报字典已填好，直接调用现有 Presence · 上报。"),
                action("dictionary", WFItems=dictionary({"kind": "phone_app", "app": app["app"], "event": event}), UUID=uid(key)),
                action("runworkflow", WFWorkflowName=REPORTER, WFInput=ref(key), WFShowWorkflow=False),
            ]
            w = workflow(name, actions)
            w["WFWorkflowClientVersion"] = "5037.0.19"  # 本机原生空 App 自动化导出格式
            w["WFWorkflowInputContentItemClasses"] = []
            w["WFWorkflowTriggers"] = [{
                "WFTriggerIdentifier": "WFAppInFocusTrigger",
                # 原生默认是 opened；closed 取自本机不含 App 选择的导出样例。
                # 不写 WFSelectedApps，明确留空，不绑定 Mac App 或猜测 iPhone bundle ID。
                "WFTriggerSerializedParameters": {"WFAppState": "closed"} if event == "close" else {},
                "WFTriggerUUID": str(uuid4()).upper(),
            }]
            result[name] = w
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sign", action="store_true", help="用 macOS shortcuts 签名，生成可导入文件")
    parser.add_argument("--reporter-only", action="store_true", help="只生成/签名共用上报")
    parser.add_argument("--automations", action="store_true", help="生成带空App触发器的OS27自动化模板")
    args = parser.parse_args()
    base = ROOT / "automations" if args.automations else ROOT
    source = base / "source"
    source.mkdir(parents=True, exist_ok=True)
    catalog = automation_workflows() if args.automations else workflows()
    for name, w in catalog.items():
        if args.reporter_only and name != REPORTER:
            continue
        path = source / (name + ".shortcut")
        path.write_bytes(plistlib.dumps(w, sort_keys=False))
        if args.sign:
            output = base / "build"
            output.mkdir(exist_ok=True)
            subprocess.run(["/usr/bin/shortcuts", "sign", "--mode", "anyone", "--input", str(path),
                            "--output", str(output / path.name)], check=True, timeout=60)
        print(name)


if __name__ == "__main__":
    main()
