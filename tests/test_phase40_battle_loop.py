"""
Phase 40 — 메뉴 드라이버 통합 테스트

CLI의 run_battle_cli는 input()을 사용하므로,
빌딩블록(build_turn_menu, build_item_use_skill, apply_skill 등)을 직접 조합해
전체 플로우를 통합 테스트한다.

1. 루트 4개 → 기본행동 → 스킬 선택 → 대상 입력 → 실행
2. 아이템사용 → build_item_use_skill → 대상 → 실행
3. 턴종료 → end_turn
4. disabled 아이템은 시도 불가
5. 모든 루트 disabled → 자동 턴종료
6. DOWN → 자동 턴종료
7. 행동 불능(슬롯 소진) → 자동 턴종료
"""
from __future__ import annotations

import pytest
from dataclasses import replace

from battle_system.core.types import CombatantID
from battle_system.core.models import (
    BattleState, CharacterDef, CombatantState, Stats,
    ItemDef, UseSkillDef,
)
from battle_system.core.commands import Skill, Step
from battle_system.engine.engine import BattleEngine
from battle_system.app.menu_builder import build_turn_menu
from battle_system.app.menu_model import TurnMenu
from battle_system.app.skill_ui import (
    get_skill_availability,
    build_item_use_skill,
    instantiate_skill_with_inputs,
)


# =============================================================================
# Helpers
# =============================================================================

def _stats() -> Stats:
    return Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10)


def _char(cid: str) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=1, stats=_stats(), max_hp=100)


def _mk_battle_2v1() -> tuple[BattleEngine, BattleState]:
    eng = BattleEngine()
    bs = eng.create_battle(allies=[_char("hero"), _char("npc")], enemies=[_char("goblin")])
    return eng, bs


def _advance_to(eng: BattleEngine, bs: BattleState, name: str) -> CombatantID:
    while str(bs.current_actor_id()) != name:
        eng.end_turn(bs)
    return bs.current_actor_id()


def _basic_skills(actor: CombatantID) -> list[Skill]:
    return [
        Skill(skill_id="BASIC_ATTACK", name="Basic Attack", actor=actor,
              action_type="MAIN", target_filter="ENEMY",
              steps=[Step(kind="ATTACK", target=None, range="ANY", area="SINGLE")]),
        Skill(skill_id="ENGAGE", name="Engage", actor=actor,
              action_type="MAIN",
              steps=[Step(kind="MOVE_ENGAGE", target=None)]),
        Skill(skill_id="DISENGAGE", name="Disengage", actor=actor,
              action_type="MAIN",
              steps=[Step(kind="MOVE_DISENGAGE")]),
        Skill(skill_id="ESCAPE", name="Escape", actor=actor,
              action_type="MAIN",
              steps=[Step(kind="TACTICAL_ESCAPE")]),
    ]


def _unique_skill(actor: CombatantID) -> Skill:
    return Skill(
        skill_id="FIRE_SLASH", name="Fire Slash", actor=actor,
        action_type="MAIN", target_filter="ENEMY",
        steps=[Step(kind="ATTACK", target=None, range="ANY", area="SINGLE")],
    )


def _heal_usd() -> UseSkillDef:
    return UseSkillDef(
        skill_id="USE_HEAL",
        name="Healing Potion",
        action_type="SUB",
        target_filter="SELF",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=10, range="ANY", area="SINGLE")],
    )


# =============================================================================
# 1. 기본행동 → 스킬 선택 → 대상 → 실행 (풀 플로우)
# =============================================================================

def test_full_flow_basic_attack() -> None:
    """기본행동 → BASIC_ATTACK → 타겟 goblin → 실행 → HP 감소."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    skills = _basic_skills(actor)
    goblin = CombatantID("goblin")
    initial_hp = bs.combatants[goblin].hp

    # 메뉴 구성
    menu = build_turn_menu(bs, actor, skills, items_registry={})
    basic = menu.get_node("BASIC")
    assert basic.enabled is True

    # BASIC_ATTACK 선택
    atk = [i for i in basic.items if i.kind == "BASIC_ATTACK"][0]
    assert atk.enabled is True

    sk = atk.payload
    concrete = instantiate_skill_with_inputs(sk, target=goblin)
    outcome = eng.apply_skill(bs, concrete)

    # 공격이 실행되었으므로 이벤트가 있어야 함
    assert len(outcome.events) > 0
    # HP가 변했는지 확인 (데미지가 0일 수도 있지만 이벤트는 있어야 함)
    assert bs.combatants[goblin].hp <= initial_hp


# =============================================================================
# 2. 아이템사용 → build_item_use_skill → 실행
# =============================================================================

def test_full_flow_item_use_heal() -> None:
    """아이템사용 → 힐링물약 선택 → build_item_use_skill → 자기자신 대상 → HP 회복."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    bs.combatants[actor].hp = 50  # HP 낮춤
    bs.inventory_snapshot[actor] = {"HEAL_POT": 2}
    bs.items["HEAL_POT"] = ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())

    registry = {"HEAL_POT": ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())}

    # 메뉴 구성
    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)
    item_use = menu.get_node("ITEM_USE")
    assert item_use.enabled is True
    assert len(item_use.items) == 1
    assert item_use.items[0].enabled is True

    # 아이템 선택 → Skill 생성
    item_id = item_use.items[0].payload
    idef = registry[item_id]
    sk = build_item_use_skill(actor=actor, item_id=item_id, use_skill_def=idef.use_skill)

    # 대상 = 자기자신
    concrete = instantiate_skill_with_inputs(sk, target=actor)
    outcome = eng.apply_skill(bs, concrete)

    # HP 회복 확인 (50 + 10 = 60)
    assert bs.combatants[actor].hp == 60

    # 아이템 소모 확인
    assert bs.inventory_snapshot[actor].get("HEAL_POT", 0) == 1

    # 이벤트에 ITEM_CONSUMED 포함
    assert "ITEM_CONSUMED" in " ".join(outcome.events)


# =============================================================================
# 3. 턴종료 → end_turn
# =============================================================================

def test_end_turn_via_menu() -> None:
    """턴종료 노드 선택 → end_turn 호출."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry={})
    end_turn = menu.get_node("END_TURN")
    assert end_turn.enabled is True

    # end_turn 실행
    eng.end_turn(bs)

    # hero가 더 이상 current actor가 아님
    assert bs.current_actor_id() != actor


# =============================================================================
# 4. disabled 아이템 → 시도 불가
# =============================================================================

def test_item_use_disabled_no_stock() -> None:
    """수량 0 아이템은 메뉴에서 disabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")
    bs.inventory_snapshot[actor] = {"HEAL_POT": 0}

    registry = {"HEAL_POT": ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())}

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)
    item_use = menu.get_node("ITEM_USE")

    assert item_use.items[0].enabled is False
    assert item_use.items[0].reason == "NO_STOCK"


def test_item_use_disabled_no_use_skill() -> None:
    """use_skill 없는 아이템은 메뉴에서 disabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")
    bs.inventory_snapshot[actor] = {"IRON_SWORD": 1}

    registry = {"IRON_SWORD": ItemDef(item_id="IRON_SWORD", weight=4, weapon_type="SWORD")}

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)
    item_use = menu.get_node("ITEM_USE")

    assert item_use.items[0].enabled is False
    assert item_use.items[0].reason == "NO_USE_SKILL"


# =============================================================================
# 5. 모든 루트 disabled → 자동 턴종료 판정
# =============================================================================

def test_auto_end_when_all_roots_disabled() -> None:
    """MAIN/SUB 슬롯 소진 + 아이템 없음 → 모든 루트 disabled 감지."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    # 슬롯 소진
    bs.combatants[actor].can_main = False
    bs.combatants[actor].can_sub = False
    bs.inventory_snapshot[actor] = {}

    skills = _basic_skills(actor)  # 모두 MAIN
    menu = build_turn_menu(bs, actor, skills, items_registry={})

    actionable = [n for n in menu.nodes if n.kind != "END_TURN" and n.enabled]
    assert len(actionable) == 0  # 자동 턴종료 조건


# =============================================================================
# 6. DOWN → 자동 턴종료
# =============================================================================

def test_auto_end_when_down() -> None:
    """DOWN 상태이면 자동 턴종료."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    bs.combatants[actor].hp = 0  # DOWN

    assert bs.combatants[actor].is_down is True

    # CLI에서는 is_down → auto end_turn
    eng.end_turn(bs)
    assert bs.current_actor_id() != actor


# =============================================================================
# 7. 연속 행동: MAIN 사용 후 SUB 슬롯 남음
# =============================================================================

def test_main_used_sub_still_available() -> None:
    """MAIN 사용 후 SUB 슬롯 남아있으면 아이템 사용 가능."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    bs.inventory_snapshot[actor] = {"HEAL_POT": 1}
    bs.items["HEAL_POT"] = ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())
    registry = {"HEAL_POT": ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())}

    # MAIN 사용(basic attack)
    goblin = CombatantID("goblin")
    atk = Skill(skill_id="BASIC_ATTACK", name="Basic Attack", actor=actor,
                action_type="MAIN", target_filter="ENEMY",
                steps=[Step(kind="ATTACK", target=goblin, range="ANY", area="SINGLE")])
    eng.apply_skill(bs, atk)

    assert bs.combatants[actor].can_main is False
    assert bs.combatants[actor].can_sub is True

    # SUB 슬롯으로 아이템 사용 가능한지 확인
    bs.combatants[actor].hp = 50
    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)

    # 기본행동(MAIN)은 disabled
    basic = menu.get_node("BASIC")
    assert basic.enabled is False

    # 아이템사용(SUB)은 enabled
    item_use = menu.get_node("ITEM_USE")
    assert item_use.enabled is True


# =============================================================================
# 8. 아이템 소모 후 메뉴 업데이트
# =============================================================================

def test_item_menu_updates_after_consumption() -> None:
    """아이템 사용 후 메뉴를 다시 빌드하면 수량 반영."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    bs.combatants[actor].hp = 50
    bs.inventory_snapshot[actor] = {"HEAL_POT": 1}
    bs.items["HEAL_POT"] = ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())
    registry = {"HEAL_POT": ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_usd())}

    # 아이템 사용
    sk = build_item_use_skill(actor=actor, item_id="HEAL_POT", use_skill_def=_heal_usd())
    concrete = instantiate_skill_with_inputs(sk, target=actor)
    eng.apply_skill(bs, concrete)

    assert bs.inventory_snapshot[actor].get("HEAL_POT", 0) == 0

    # 메뉴 재구성
    menu2 = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)
    item_use = menu2.get_node("ITEM_USE")

    # 수량 0 → 아이템사용 메뉴 disabled (items가 비거나 모든 항목 disabled)
    assert item_use.enabled is False


# =============================================================================
# 9. 메뉴에 "대상 선택 disabled 표시" 반영
# =============================================================================

def test_target_options_show_disabled_in_flow() -> None:
    """ENEMY 필터 스킬: 아군은 disabled, 적은 enabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to(eng, bs, "hero")

    from battle_system.app.skill_ui import list_target_options

    sk = Skill(
        skill_id="BASIC_ATTACK", name="Basic Attack", actor=actor,
        action_type="MAIN", target_filter="ENEMY",
        steps=[Step(kind="ATTACK", target=None, range="ANY", area="SINGLE")],
    )

    opts = list_target_options(bs, actor, sk)
    by_id = {str(o.target_id): o for o in opts}

    assert by_id["goblin"].enabled is True

    if "npc" in by_id:
        assert by_id["npc"].enabled is False
        assert by_id["npc"].reason == "WRONG_TEAM"
