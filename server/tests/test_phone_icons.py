"""公开手机状态所支持的每个 App 都有可用的同站图标。"""
import json
import re
import struct
from pathlib import Path

from presence.rules import Rules

ROOT = Path(__file__).resolve().parents[2]


def test_all_phone_apps_have_frontend_icons_and_source_records():
    rules = Rules.load(ROOT / 'server/config/rules.yaml')
    icon_dir = ROOT / 'web/public/icons/apps'
    records = json.loads((icon_dir / 'sources.json').read_text())
    frontend = (ROOT / 'web/public/app.js').read_text()
    block = frontend.split('const PHONE_ICONS = {', 1)[1].split('};', 1)[0]
    pairs = re.findall(r'(?:"([^"]+)"|([\w\u4e00-\u9fff]+)):\s*"([a-z-]+)"', block)
    mapping = {(quoted or bare): slug for quoted, bare, slug in pairs}
    assert set(mapping) == set(records) == set(rules.phone_apps)
    assert len(mapping) == 21
    for app, slug in mapping.items():
        assert records[app]['file'] == slug + '.png'
        assert records[app]['source'].startswith('https://apps.apple.com/')
        data = (icon_dir / (slug + '.png')).read_bytes()
        assert data.startswith(b'\x89PNG\r\n\x1a\n')
        assert struct.unpack('>II', data[16:24]) == (128, 128)
