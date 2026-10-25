"""OS 27 自动化模板：只将触发器的 App 选择留空，动作与事件全部预填。"""
import json
import plistlib
from pathlib import Path
from uuid import UUID

import pytest

from presence.rules import Rules

ROOT = Path(__file__).resolve().parents[2]
AUTOMATIONS = ROOT / 'shortcuts' / 'automations'
APPS = json.loads((AUTOMATIONS / 'apps.json').read_text())
CASES = [(app, event, verb) for app in APPS for event, verb in [('open', '打开'), ('close', '退出')]]


@pytest.mark.parametrize('app,event,verb', CASES)
def test_named_automation_has_empty_app_trigger_and_ready_report(app, event, verb):
    title = f"Presence · {app['name']} · {verb}"
    w = plistlib.loads((AUTOMATIONS / 'source' / f'{title}.shortcut').read_bytes())
    assert w['WFWorkflowName'] == title
    trigger, = w['WFWorkflowTriggers']
    assert trigger['WFTriggerIdentifier'] == 'WFAppInFocusTrigger'
    assert trigger['WFTriggerSerializedParameters'] == ({'WFAppState': 'closed'} if event == 'close' else {})
    trigger_id = trigger['WFTriggerUUID']
    assert str(UUID(trigger_id)).upper() == trigger_id and UUID(trigger_id).version == 4
    # 不写任何 Mac/iPhone App 令牌或通配选择；由用户在手机上选择实际 App。
    assert 'WFSelectedApps' not in trigger['WFTriggerSerializedParameters']
    assert not w['WFWorkflowImportQuestions']  # 不索取令牌，复用现有上报
    steps = w['WFWorkflowActions']
    assert [step['WFWorkflowActionIdentifier'] for step in steps] == [
        'is.workflow.actions.comment', 'is.workflow.actions.dictionary', 'is.workflow.actions.runworkflow']
    data = steps[1]['WFWorkflowActionParameters']
    fields = data['WFItems']['Value']['WFDictionaryFieldValueItems']
    values = {field['WFKey']['Value']['string']: field['WFValue']['Value']['string'] for field in fields}
    assert values == {'kind': 'phone_app', 'app': app['app'], 'event': event}
    report = steps[2]['WFWorkflowActionParameters']
    assert report['WFWorkflowName'] == 'Presence · 上报'
    assert report['WFInput']['Value']['OutputUUID'] == data['UUID']
    assert report['WFShowWorkflow'] is False


def test_automation_catalog_is_complete_and_trigger_ids_are_unique():
    files = list((AUTOMATIONS / 'source').glob('*.shortcut'))
    assert len(APPS) == 21 and len(files) == 42
    rules = Rules.load(ROOT / 'server' / 'config' / 'rules.yaml')
    assert {app['app'] for app in APPS} == set(rules.phone_apps)
    ids = [plistlib.loads(file.read_bytes())['WFWorkflowTriggers'][0]['WFTriggerUUID'] for file in files]
    assert len(ids) == len(set(ids))
