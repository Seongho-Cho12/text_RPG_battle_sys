# battle_system/app/menu_builder.py
"""
Phase 39: 4단 메뉴 빌더.

build_turn_menu()는 현재 전투 상태에서 한 턴의 전체 메뉴를 구성한다.
루트 4개: 기본행동 / 고유행동 / 아이템사용 / 턴종료
"""
from __future__ import annotations

from typing import Dict, List, Optional

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState, ItemDef
from battle_system.core.commands import Skill
from battle_system.app.skill_ui import (
    get_skill_availability,
    list_use_items,
    list_throw_items,
    build_item_use_skill,
    SkillAvailability,
)
from battle_system.app.menu_model import (
    MenuItem, MenuNode, TurnMenu, MenuKind, RootKind,
)


# 기본행동에 해당하는 skill_id 집합
_BASIC_SKILL_IDS = frozenset({
    "BASIC_ATTACK",
    "ENGAGE",
    "DISENGAGE",
    "ESCAPE",
})

# skill_id → MenuKind 매핑
_BASIC_SKILL_KIND: Dict[str, MenuKind] = {
    "BASIC_ATTACK": "BASIC_ATTACK",
    "ENGAGE": "ENGAGE",
    "DISENGAGE": "DISENGAGE",
    "ESCAPE": "ESCAPE",
}


def build_turn_menu(
    bs: BattleState,
    actor: CombatantID,
    skills: List[Skill],
    items_registry: Dict[str, ItemDef],
) -> TurnMenu:
    """
    현재 전투 상태에서 한 턴의 전체 메뉴를 구성.

    Parameters
    ----------
    bs : BattleState
    actor : 현재 행동자
    skills : 이 actor의 모든 스킬 (basic + unique)
    items_registry : 전체 아이템 정의(use_skill 포함)
    """
    basic_node = _build_basic_node(bs, actor, skills)
    unique_node = _build_unique_node(bs, actor, skills)
    item_use_node = _build_item_use_node(bs, actor, items_registry)
    end_turn_node = MenuNode(title="턴종료", kind="END_TURN", items=[])

    return TurnMenu(
        actor=str(actor),
        nodes=[basic_node, unique_node, item_use_node, end_turn_node],
    )


def _build_basic_node(
    bs: BattleState,
    actor: CombatantID,
    skills: List[Skill],
) -> MenuNode:
    """기본행동: basic_attack/engage/disengage/escape + throw."""
    items: List[MenuItem] = []

    for sk in skills:
        if sk.actor != actor:
            continue
        if sk.skill_id not in _BASIC_SKILL_IDS:
            continue

        av = get_skill_availability(bs, sk)
        kind = _BASIC_SKILL_KIND.get(sk.skill_id, "BASIC_ATTACK")
        items.append(MenuItem(
            label=sk.name,
            enabled=av.usable,
            reason=av.reason,
            kind=kind,
            payload=sk,
        ))

    # 투척: 투척 가능 아이템이 있어야 enabled
    throw_items = list_throw_items(bs, actor)
    has_throw_items = any(t.enabled for t in throw_items)

    # 투척 스킬 찾기 (steps에 TACTICAL_THROW가 있는 스킬)
    throw_skill = None
    for sk in skills:
        if sk.actor != actor:
            continue
        for s in (sk.steps or []):
            if s.kind == "TACTICAL_THROW":
                throw_skill = sk
                break
        if throw_skill:
            break

    if throw_skill is not None:
        av = get_skill_availability(bs, throw_skill)
        throw_enabled = av.usable and has_throw_items
        throw_reason = av.reason if not av.usable else ("OK" if has_throw_items else "NO_THROWABLE_ITEM")
        items.append(MenuItem(
            label=throw_skill.name if throw_skill else "투척",
            enabled=throw_enabled,
            reason=throw_reason,
            kind="THROW",
            payload=throw_skill,
        ))
    else:
        # throw 스킬이 아예 없으면 비활성 항목으로 표시
        items.append(MenuItem(
            label="투척",
            enabled=False,
            reason="NO_THROWABLE_ITEM" if not has_throw_items else "MISSING_INPUT",
            kind="THROW",
            payload=None,
        ))

    return MenuNode(title="기본행동", kind="BASIC", items=items)


def _build_unique_node(
    bs: BattleState,
    actor: CombatantID,
    skills: List[Skill],
) -> MenuNode:
    """고유행동: 기본행동이 아닌 개인 스킬들."""
    items: List[MenuItem] = []

    for sk in skills:
        if sk.actor != actor:
            continue
        if sk.skill_id in _BASIC_SKILL_IDS:
            continue
        # USE: prefix는 아이템 사용 스킬이므로 제외
        if sk.skill_id.startswith("USE:"):
            continue

        av = get_skill_availability(bs, sk)
        items.append(MenuItem(
            label=sk.name,
            enabled=av.usable,
            reason=av.reason,
            kind="UNIQUE_SKILL",
            payload=sk,
        ))

    return MenuNode(title="고유행동", kind="UNIQUE", items=items)


def _build_item_use_node(
    bs: BattleState,
    actor: CombatantID,
    items_registry: Dict[str, ItemDef],
) -> MenuNode:
    """아이템사용: 인벤의 아이템을 use_skill 유무 + 수량으로 enabled/disabled."""
    items: List[MenuItem] = []

    use_items = list_use_items(bs, actor, items_registry)
    for opt in use_items:
        idef = items_registry.get(opt.item_id)
        label = opt.item_id
        if idef and idef.use_skill:
            label = idef.use_skill.name

        items.append(MenuItem(
            label=f"{label} x{opt.quantity}",
            enabled=opt.enabled,
            reason=opt.reason,
            kind="USE_ITEM",
            payload=opt.item_id,
        ))

    return MenuNode(title="아이템사용", kind="ITEM_USE", items=items)
