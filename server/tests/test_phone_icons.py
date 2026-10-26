"""公开手机状态所支持的每个 App 都有可用的同站图标。"""
import json
import struct
from pathlib import Path

from presence.rules import Rules

ROOT = Path(__file__).resolve().parents[2]


def test_all_phone_apps_have_frontend_icons_and_source_records():
    rules = Rules.load(ROOT / 'server/config/rules.yaml')
    icon_dir = ROOT / 'web/public/icons/apps'
    records = json.loads((icon_dir / 'sources.json').read_text())
    assert set(records) == set(rules.phone_apps)
    assert len(rules.phone_apps) == 21
    for app, info in rules.phone_apps.items():
        assert records[app]['file'] == f'{info.icon}.png'
        assert records[app]['source'].startswith('https://apps.apple.com/')
        data = (icon_dir / f'{info.icon}.png').read_bytes()
        assert data.startswith(b'\x89PNG\r\n\x1a\n')
        assert struct.unpack('>II', data[16:24]) == (128, 128)


def test_video_sites_and_players_have_names():
    rules = Rules.load(ROOT / 'server/config/rules.yaml')
    assert rules.video_sites == {'bilibili': 'B站', 'youtube': 'YouTube'}
    assert rules.site_names['bilibili.com'] == 'B站'
    assert rules.players == {'apple_music': 'Apple Music', 'aimp': 'AIMP'}
    for player in rules.players:
        assert (ROOT / f"web/public/icons/{player.replace('_', '-')}.svg").exists()
