# battle_system/app/battle_loop_cli.py
"""
Phase 40: 메뉴 드라이버 방식 CLI 전투 루프.

흐름(턴마다):
1. build_turn_menu()로 4단 메뉴 구성
2. 루트 선택 (기본행동/고유행동/아이템사용/턴종료)
3. 하위 선택
4. 필요한 입력 (대상/아이템 등)
5. engine.apply_skill() 또는 end_turn
6. 행동 슬롯 소진/사용 가능 스킬 없음/다운 → 자동 턴 종료
"""
from __future__ import annotations

from typing import Dict, List, Optional

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState, ItemDef
from battle_system.core.commands import Skill
from battle_system.engine.engine import BattleEngine

from battle_system.app.skill_ui import (
    get_skill_availability,
    instantiate_skill_with_inputs,
    build_item_use_skill,
    list_target_options,
    list_throw_items,
)
from battle_system.app.menu_builder import build_turn_menu
from battle_system.app.menu_model import MenuItem, MenuNode, TurnMenu


def run_battle_cli(
    bs: BattleState,
    *,
    engine: BattleEngine,
    skills_by_actor: Dict[CombatantID, List[Skill]],
    items_registry: Optional[Dict[str, ItemDef]] = None,
) -> BattleState:
    """
    메뉴 기반 CLI 전투 루프.

    Parameters
    ----------
    bs : BattleState — 전투 상태
    engine : BattleEngine
    skills_by_actor : actor별 스킬 목록
    items_registry : 아이템 정의 Dict (use_skill 포함)
    """
    registry = items_registry or {}

    while not bs.ended:
        actor = bs.current_actor_id()
        st = bs.combatants[actor]

        _print_turn_header(bs, actor)

        # DOWN이면 자동 턴 종료
        if st.is_down:
            print("AUTO: actor DOWN -> end_turn")
            bs = engine.end_turn(bs)
            continue

        my_skills = skills_by_actor.get(actor, [])
        menu = build_turn_menu(bs, actor, my_skills, items_registry=registry)

        # 모든 루트(턴종료 제외)가 disabled면 자동 턴 종료
        actionable = [n for n in menu.nodes if n.kind != "END_TURN" and n.enabled]
        if not actionable:
            print("AUTO: no usable actions -> end_turn")
            bs = engine.end_turn(bs)
            continue

        # 루트 메뉴 출력
        _print_root_menu(menu)

        sel = input("Select root: ").strip()

        # 턴종료 단축키
        if sel.lower() == "t":
            bs = engine.end_turn(bs)
            continue

        try:
            root_idx = int(sel)
            node = menu.nodes[root_idx]
        except (ValueError, IndexError):
            print("Invalid selection")
            continue

        if not node.enabled:
            print(f"'{node.title}' is disabled: {node.reason}")
            continue

        # 턴종료 노드
        if node.kind == "END_TURN":
            bs = engine.end_turn(bs)
            continue

        # 아이템사용 노드
        if node.kind == "ITEM_USE":
            bs = _handle_item_use(bs, engine, actor, node, registry)
            continue

        # 기본행동 / 고유행동 → 하위 스킬 선택
        bs = _handle_skill_selection(bs, engine, actor, node)

    print("\n" + "=" * 80)
    print(f"BATTLE ENDED: reason={bs.end_reason}")
    return bs


# =============================================================================
# Internal handlers
# =============================================================================

def _handle_skill_selection(
    bs: BattleState,
    engine: BattleEngine,
    actor: CombatantID,
    node: MenuNode,
) -> BattleState:
    """기본행동/고유행동 하위 스킬 선택 → 대상/아이템 입력 → 실행."""
    _print_sub_menu(node)

    sel = input("Select action: ").strip()
    try:
        idx = int(sel)
        item = node.items[idx]
    except (ValueError, IndexError):
        print("Invalid selection")
        return bs

    if not item.enabled:
        print(f"'{item.label}' is disabled: {item.reason}")
        return bs

    sk: Skill = item.payload
    if sk is None:
        print("No skill associated")
        return bs

    # 투척이면 아이템 선택 필요
    throw_item_id: Optional[str] = None
    if item.kind == "THROW":
        throw_items = list_throw_items(bs, actor)
        enabled_throws = [t for t in throw_items if t.enabled]
        if not enabled_throws:
            print("No throwable items")
            return bs

        print("\n[Throw Items]")
        for i, t in enumerate(enabled_throws):
            print(f"  [{i}] {t.item_id} x{t.quantity}")
        t_sel = input("Choose item: ").strip()
        try:
            throw_item_id = enabled_throws[int(t_sel)].item_id
        except (ValueError, IndexError):
            print("Invalid item")
            return bs

    # 대상 선택
    target: Optional[CombatantID] = None
    av = get_skill_availability(bs, sk)
    if av.spec.target_required:
        target_opts = list_target_options(bs, actor, sk)
        enabled_targets = [t for t in target_opts if t.enabled]
        if not enabled_targets:
            print("No valid targets")
            return bs

        print("\n[Targets]")
        for i, t in enumerate(target_opts):
            tag = "" if t.enabled else f" (DISABLED: {t.reason})"
            cst = bs.combatants[t.target_id]
            print(f"  [{i}] {t.target_id} hp={cst.hp}/{cst.max_hp}{tag}")

        t_sel = input("Choose target: ").strip()
        try:
            chosen_t = target_opts[int(t_sel)]
        except (ValueError, IndexError):
            print("Invalid target")
            return bs

        if not chosen_t.enabled:
            print(f"Target '{chosen_t.target_id}' is disabled: {chosen_t.reason}")
            return bs
        target = chosen_t.target_id

    concrete = instantiate_skill_with_inputs(sk, target=target, throw_item_id=throw_item_id)
    outcome = engine.apply_skill(bs, concrete)

    for e in outcome.events:
        print("  " + e)

    return bs


def _handle_item_use(
    bs: BattleState,
    engine: BattleEngine,
    actor: CombatantID,
    node: MenuNode,
    items_registry: Dict[str, ItemDef],
) -> BattleState:
    """아이템사용 노드: 아이템 선택 → build_item_use_skill → 대상 → 실행."""
    _print_sub_menu(node)

    sel = input("Select item: ").strip()
    try:
        idx = int(sel)
        item = node.items[idx]
    except (ValueError, IndexError):
        print("Invalid selection")
        return bs

    if not item.enabled:
        print(f"'{item.label}' is disabled: {item.reason}")
        return bs

    item_id = item.payload  # str
    idef = items_registry.get(item_id)
    if idef is None or idef.use_skill is None:
        print("Item has no use_skill")
        return bs

    # UseSkillDef → Skill 인스턴스
    sk = build_item_use_skill(actor=actor, item_id=item_id, use_skill_def=idef.use_skill)

    # 대상 선택
    av = get_skill_availability(bs, sk)
    if not av.usable:
        print(f"Cannot use: {av.reason}")
        return bs

    target: Optional[CombatantID] = None
    if av.spec.target_required:
        target_opts = list_target_options(bs, actor, sk)
        enabled_targets = [t for t in target_opts if t.enabled]
        if not enabled_targets:
            print("No valid targets")
            return bs

        print("\n[Targets]")
        for i, t in enumerate(target_opts):
            tag = "" if t.enabled else f" (DISABLED: {t.reason})"
            cst = bs.combatants[t.target_id]
            print(f"  [{i}] {t.target_id} hp={cst.hp}/{cst.max_hp}{tag}")

        t_sel = input("Choose target: ").strip()
        try:
            chosen_t = target_opts[int(t_sel)]
        except (ValueError, IndexError):
            print("Invalid target")
            return bs

        if not chosen_t.enabled:
            print(f"Target '{chosen_t.target_id}' is disabled: {chosen_t.reason}")
            return bs
        target = chosen_t.target_id

    # 대상 채우기
    concrete = instantiate_skill_with_inputs(sk, target=target)
    outcome = engine.apply_skill(bs, concrete)

    for e in outcome.events:
        print("  " + e)

    return bs


# =============================================================================
# Display helpers
# =============================================================================

def _print_turn_header(bs: BattleState, actor: CombatantID) -> None:
    st = bs.combatants[actor]
    print("\n" + "=" * 80)
    print(f"TICK={bs.tick} ACTOR={actor} HP={st.hp}/{st.max_hp} "
          f"can_main={st.can_main} can_sub={st.can_sub}")
    if st.cooldowns:
        print(f"COOLDOWNS: {dict(st.cooldowns)}")
    if st.effects:
        print(f"EFFECTS: {dict(st.effects)}")


def _print_root_menu(menu: TurnMenu) -> None:
    print("\n[Turn Menu]")
    for i, node in enumerate(menu.nodes):
        tag = "" if node.enabled else " (DISABLED)"
        print(f"  [{i}] {node.title}{tag}")
    print("  [T] Turn Pass")


def _print_sub_menu(node: MenuNode) -> None:
    print(f"\n[{node.title}]")
    for i, item in enumerate(node.items):
        tag = "" if item.enabled else f" (DISABLED: {item.reason})"
        print(f"  [{i}] {item.label}{tag}")
