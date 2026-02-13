"""
Phase 39 — 메뉴 트리(4단 루트) 모델링 테스트

1. 4개 루트 노드 생성 확인
2. 기본행동 하위 항목 존재 확인
3. 고유행동 하위 항목 존재 확인
4. 아이템사용 하위 항목 enabled/disabled
5. 자식 전부 disabled → 루트 disabled
6. 턴종료는 항상 enabled
7. 투척 아이템 없으면 throw disabled
8. use_skill 없는 아이템 → disabled
"""
from __future__ import annotations

import pytest
from dataclasses import replace

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import (
    BattleState, CharacterDef, CombatantState, Stats,
    ItemDef, UseSkillDef,
)
from battle_system.core.commands import Skill, Step
from battle_system.engine.engine import BattleEngine
from battle_system.app.menu_builder import build_turn_menu
from battle_system.app.menu_model import MenuNode, TurnMenu


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


def _advance_to_hero(eng: BattleEngine, bs: BattleState) -> CombatantID:
    while str(bs.current_actor_id()) != "hero":
        eng.end_turn(bs)
    return bs.current_actor_id()


def _basic_skills(actor: CombatantID) -> list[Skill]:
    """최소 기본 스킬 세트."""
    return [
        Skill(skill_id="BASIC_ATTACK", name="Basic Attack", actor=actor,
              action_type="MAIN",
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


def _heal_use_skill_def() -> UseSkillDef:
    return UseSkillDef(
        skill_id="USE_HEAL",
        name="Healing Potion",
        action_type="SUB",
        target_filter="SELF",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=10, range="ANY", area="SINGLE")],
    )


# =============================================================================
# 1. 4개 루트 노드 생성 확인
# =============================================================================

def test_turn_menu_has_four_root_nodes() -> None:
    """메뉴는 기본행동/고유행동/아이템사용/턴종료 4개 루트를 가진다."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    skills = _basic_skills(actor) + [_unique_skill(actor)]

    menu = build_turn_menu(bs, actor, skills, items_registry={})
    assert len(menu.nodes) == 4

    kinds = [n.kind for n in menu.nodes]
    assert "BASIC" in kinds
    assert "UNIQUE" in kinds
    assert "ITEM_USE" in kinds
    assert "END_TURN" in kinds


# =============================================================================
# 2. 기본행동 하위 항목
# =============================================================================

def test_basic_node_has_expected_items() -> None:
    """기본행동 노드는 basic_attack/engage/disengage/escape/throw를 포함한다."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    skills = _basic_skills(actor)

    menu = build_turn_menu(bs, actor, skills, items_registry={})
    basic = menu.get_node("BASIC")
    assert basic is not None

    item_kinds = [i.kind for i in basic.items]
    assert "BASIC_ATTACK" in item_kinds
    assert "ENGAGE" in item_kinds
    assert "DISENGAGE" in item_kinds
    assert "ESCAPE" in item_kinds
    assert "THROW" in item_kinds


def test_basic_node_enabled_when_skills_available() -> None:
    """기본 스킬이 사용 가능하면 기본행동 노드 enabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    skills = _basic_skills(actor)

    menu = build_turn_menu(bs, actor, skills, items_registry={})
    basic = menu.get_node("BASIC")
    assert basic.enabled is True


# =============================================================================
# 3. 고유행동 하위 항목
# =============================================================================

def test_unique_node_has_personal_skills() -> None:
    """고유행동 노드는 개인 스킬을 포함한다."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    skills = _basic_skills(actor) + [_unique_skill(actor)]

    menu = build_turn_menu(bs, actor, skills, items_registry={})
    unique = menu.get_node("UNIQUE")
    assert unique is not None
    assert len(unique.items) == 1
    assert unique.items[0].label == "Fire Slash"
    assert unique.items[0].kind == "UNIQUE_SKILL"


def test_unique_node_disabled_when_no_skills() -> None:
    """고유 스킬이 없으면 고유행동 노드 disabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    skills = _basic_skills(actor)  # 고유 스킬 없음

    menu = build_turn_menu(bs, actor, skills, items_registry={})
    unique = menu.get_node("UNIQUE")
    assert unique is not None
    assert unique.enabled is False
    assert unique.reason == "ALL_CHILDREN_DISABLED"


# =============================================================================
# 4. 아이템사용 노드
# =============================================================================

def test_item_use_node_enabled_items() -> None:
    """use_skill 있는 아이템은 enabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"HEAL_POT": 2}

    registry = {
        "HEAL_POT": ItemDef(
            item_id="HEAL_POT", weight=1,
            use_skill=_heal_use_skill_def(),
        ),
    }

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)
    item_use = menu.get_node("ITEM_USE")
    assert item_use is not None
    assert item_use.enabled is True
    assert len(item_use.items) == 1
    assert item_use.items[0].enabled is True
    assert "x2" in item_use.items[0].label


def test_item_use_node_disabled_no_use_skill() -> None:
    """use_skill 없는 아이템은 disabled (NO_USE_SKILL)."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"IRON_SWORD": 1}

    registry = {
        "IRON_SWORD": ItemDef(item_id="IRON_SWORD", weight=4, weapon_type="SWORD"),
    }

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry=registry)
    item_use = menu.get_node("ITEM_USE")
    assert item_use.items[0].enabled is False
    assert item_use.items[0].reason == "NO_USE_SKILL"


def test_item_use_node_disabled_when_empty_inventory() -> None:
    """인벤토리가 비면 아이템사용 노드 disabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {}

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry={})
    item_use = menu.get_node("ITEM_USE")
    assert item_use.enabled is False


# =============================================================================
# 5. 자식 전부 disabled → 루트 disabled
# =============================================================================

def test_root_disabled_when_all_children_disabled() -> None:
    """모든 스킬이 쿨다운 등으로 disabled면 루트 노드도 disabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)

    # MAIN 슬롯을 소모시킴 → 모든 MAIN 스킬 disabled
    bs.combatants[actor].can_main = False

    skills = _basic_skills(actor)  # 모두 MAIN 스킬
    menu = build_turn_menu(bs, actor, skills, items_registry={})

    basic = menu.get_node("BASIC")
    # basic의 모든 항목: basic_attack/engage/disengage/escape는 MAIN → disabled
    # throw는 아이템 없어서 disabled
    for item in basic.items:
        assert item.enabled is False, f"{item.label} should be disabled"

    assert basic.enabled is False
    assert basic.reason == "ALL_CHILDREN_DISABLED"


# =============================================================================
# 6. 턴종료는 항상 enabled
# =============================================================================

def test_end_turn_always_enabled() -> None:
    """턴종료 노드는 항상 enabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry={})
    end_turn = menu.get_node("END_TURN")
    assert end_turn is not None
    assert end_turn.enabled is True


# =============================================================================
# 7. 투척 아이템 없으면 throw disabled
# =============================================================================

def test_throw_disabled_when_no_items() -> None:
    """투척 아이템이 없으면 throw 항목 disabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {}

    menu = build_turn_menu(bs, actor, _basic_skills(actor), items_registry={})
    basic = menu.get_node("BASIC")

    throw_items = [i for i in basic.items if i.kind == "THROW"]
    assert len(throw_items) == 1
    assert throw_items[0].enabled is False
