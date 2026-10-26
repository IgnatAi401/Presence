"""分类规则：加载 rules.yaml，把 Mac 前台信息归到某个活动类别。"""

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
class Classified:
    """一次 desktop 信号的分类结果。category 可能是 REFERENCE，交给引擎按上下文决定。"""

    category: str
    project: str | None = None
    domain: str | None = None


@dataclass
class Rules:
    anchors: dict[str, AnchorDef]
    activities: dict[str, ActivityDef]
    projects: dict[str, str] = field(default_factory=dict)
    domains: dict[str, str] = field(default_factory=dict)
    apps: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    phone_apps: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Rules:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            anchors={k: AnchorDef(**v) for k, v in raw.get("anchors", {}).items()},
            activities={k: ActivityDef(**v) for k, v in raw.get("activities", {}).items()},
            projects=raw.get("projects") or {},
            domains={k.lower(): v for k, v in (raw.get("domains") or {}).items()},
            apps=raw.get("apps") or {},
            names=raw.get("names") or {},
            phone_apps=raw.get("phone_apps") or {},
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

        项目的分类优先用小工具设置里选的（data.category），只接受公开显示项目名的活动（科研 / 开发）；
        没选时再查 rules.yaml 的 projects。
        """
        project = data.get("project") or None
        chosen = data.get("category") or None
        domain = (data.get("domain") or "").lower() or None
        bundle = data.get("bundle_id") or ""

        if data.get("private") or data.get("hidden"):
            return Classified("browse")  # 私有网站公开状态只算上网，不按具体域名分类

        if project and chosen and (a := self.activities.get(chosen)) and a.show_project:
            return Classified(chosen, project=project)
        if project and project in self.projects:
            return Classified(self.projects[project], project=project)
        if domain:
            hit = self.match_domain(domain)
            if hit:
                return Classified(hit[1], domain=hit[0])
            return Classified(REFERENCE, domain=domain)
        if bundle in self.apps:
            return Classified(self.apps[bundle], project=project)
        return None
