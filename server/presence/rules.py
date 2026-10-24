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


@dataclass(frozen=True)
class ActivityDef:
    tier: int
    label: str
    short: str = ""
    serious: bool = False


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
        )

    def is_serious(self, category: str | None) -> bool:
        a = self.activities.get(category or "")
        return bool(a and a.serious)

    def tier(self, category: str) -> int:
        a = self.activities.get(category)
        return a.tier if a else 9

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
        """按 项目 → 域名 → App 默认值 的顺序匹配；都未命中返回 None（沿用上一个活动）。"""
        project = data.get("project") or None
        domain = (data.get("domain") or "").lower() or None
        bundle = data.get("bundle_id") or ""

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
