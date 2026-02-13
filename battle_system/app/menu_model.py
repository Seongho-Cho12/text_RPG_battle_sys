# battle_system/app/menu_model.py
"""
Phase 39: 4단 메뉴 트리 모델.

루트 메뉴: 기본행동 / 고유행동 / 아이템사용 / 턴종료
각 루트는 하위 MenuItem 리스트를 가짐.
자식이 전부 disabled → 루트도 disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional


MenuKind = Literal[
    # 기본행동 하위
    "BASIC_ATTACK",
    "ENGAGE",
    "DISENGAGE",
    "THROW",
    "ESCAPE",
    # 고유행동 하위
    "UNIQUE_SKILL",
    # 아이템사용 하위
    "USE_ITEM",
    # 턴종료
    "END_TURN",
]

RootKind = Literal[
    "BASIC",
    "UNIQUE",
    "ITEM_USE",
    "END_TURN",
]


@dataclass(frozen=True)
class MenuItem:
    """하위 메뉴 항목 (개별 행동/스킬/아이템)."""
    label: str
    enabled: bool
    reason: str            # disabled 사유 (enabled이면 "OK")
    kind: MenuKind
    payload: Any = None    # Skill, item_id 등 실행에 필요한 데이터


@dataclass(frozen=True)
class MenuNode:
    """루트 메뉴 노드."""
    title: str
    kind: RootKind
    items: List[MenuItem] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        """자식 중 하나라도 enabled면 True, 전부 disabled면 False."""
        if not self.items:
            # 턴종료처럼 items가 비어있어도 enabled인 경우 있음
            return self.kind == "END_TURN"
        return any(item.enabled for item in self.items)

    @property
    def reason(self) -> str:
        if self.enabled:
            return "OK"
        return "ALL_CHILDREN_DISABLED"


@dataclass(frozen=True)
class TurnMenu:
    """한 턴의 전체 메뉴 트리."""
    actor: str
    nodes: List[MenuNode]

    def get_node(self, kind: RootKind) -> Optional[MenuNode]:
        for n in self.nodes:
            if n.kind == kind:
                return n
        return None
