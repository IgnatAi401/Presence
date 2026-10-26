"""分类规则：加载 rules.yaml，把 Mac 前台信息归到某个活动类别，并提供各处显示用的名字和图标。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REFERENCE = "reference"


@dataclass(frozen=True)
class AnchorDef:
    rank: int
    label: str
    short: str = ""
    activity: str | None = None  # 同类活动合并进这个锚定状态显示


@dataclass(frozen=True)
class ActivityDef:
    tier: int
    label: str
    short: str = ""
    serious: bool = False
    keep_min: float = 0  # 离开后继续显示多久（余温）
    show_project: bool = False  # 公开显示工作区文件夹名（项目名）


@dataclass(frozen=True)
class PhoneApp:
    label: str  # 公开文案
    icon: str | None = None  # web/public/icons/apps/<icon>.png


@dataclass(frozen=True)
class Classified:
    """一次 desktop 信号的分类结果。category 可能是 REFERENCE，交给引擎按上下文决定。"""

    category: str
    project: str | None = None
    domain: str | None = None


@dataclass
class Rules:
    anchors: dict[str, AnchorDef]
    activities: dict[str, ActivityDef]
    domains: dict[str, str] = field(default_factory=dict)  # 域名 → 类别
    apps: dict[str, str] = field(default_factory=dict)  # bundle id → 类别
    site_names: dict[str, str] = field(default_factory=dict)  # 域名 → 显示名
    video_sites: dict[str, str] = field(default_factory=dict)  # 视频信号的 site → 显示名
    phone_apps: dict[str, PhoneApp] = field(default_factory=dict)
    players: dict[str, str] = field(default_factory=dict)  # 音乐信号的 player → 显示名

    @classmethod
    def load(cls, path: Path) -> Rules:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        domains, site_names, video_sites = {}, {}, {}
        for k, v in (raw.get("domains") or {}).items():
            k = k.lower()
            if isinstance(v, str):
                domains[k] = v
                continue
            domains[k] = v["category"]
            if name := v.get("name"):
                site_names[k] = name
                if v.get("video"):
                    video_sites[v["video"]] = name
        return cls(
            anchors={k: AnchorDef(**v) for k, v in raw.get("anchors", {}).items()},
            activities={k: ActivityDef(**v) for k, v in raw.get("activities", {}).items()},
            domains=domains,
            apps=raw.get("apps") or {},
            site_names=site_names,
            video_sites=video_sites,
            phone_apps={k: PhoneApp(**v) for k, v in (raw.get("phone_apps") or {}).items()},
            players=raw.get("players") or {},
        )

    def is_serious(self, category: str | None) -> bool:
        a = self.activities.get(category or "")
        return bool(a and a.serious)

    def tier(self, category: str) -> int:
        a = self.activities.get(category)
        return a.tier if a else 9

    def keep_sec(self, category: str) -> float:
        a = self.activities.get(category)
        return a.keep_min * 60 if a else 0

    def short(self, category: str) -> str:
        d = self.anchors.get(category) or self.activities.get(category)
        return (d.short if d else "") or category

    def match_domain(self, domain: str) -> tuple[str, str] | None:
        """返回 (命中的规则域名, 类别)；子域名逐级向上匹配。"""
        parts = domain.lower().strip(".").split(".")
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in self.domains:
                return candidate, self.domains[candidate]
        return None

    def classify_desktop(self, data: dict) -> Classified | None:
        """按 项目 → 域名 → App 默认值 的顺序匹配；都未命中返回 None（沿用上一个活动）。

        项目的分类用小工具设置里选的（data.category），只接受公开显示项目名的活动（科研 / 开发）；
        没选时按 App 默认值算。
        """
        project = data.get("project") or None
        chosen = data.get("category") or None
        domain = (data.get("domain") or "").lower() or None
        bundle = data.get("bundle_id") or ""

        if data.get("private"):
            return Classified("browse")  # 私有网站公开状态只算上网，不按具体域名分类

        if project and chosen and (a := self.activities.get(chosen)) and a.show_project:
            return Classified(chosen, project=project)
        if domain:
            hit = self.match_domain(domain)
            if hit:
                return Classified(hit[1], domain=hit[0])
            return Classified(REFERENCE, domain=domain)
        if bundle in self.apps:
            return Classified(self.apps[bundle], project=project)
        return None
