"""
Phase 38 — Availability 계층 통합 테스트

1. get_skill_availability: consume_item_id 수량 체크 (NO_ITEM_STOCK)
2. list_use_items: use_skill 유무 + 수량으로 enabled/disabled
3. list_throw_items: 수량으로 enabled/disabled
4. list_target_options: DOWN/WRONG_TEAM/OUT_OF_RANGE/OK 판별
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
from battle_system.app.skill_ui import (
    get_skill_availability,
    build_item_use_skill,
    list_use_items,
    list_throw_items,
    list_target_options,
    ItemOption,
    TargetOption,
)


# =============================================================================
# Helpers
# =============================================================================

def _stats() -> Stats:
    return Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10)


def _char(cid: str) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=1, stats=_stats(), max_hp=100)


def _mk_battle_1v1() -> tuple[BattleEngine, BattleState]:
    eng = BattleEngine()
    bs = eng.create_battle(allies=[_char("hero")], enemies=[_char("goblin")])
    return eng, bs


def _mk_battle_2v1() -> tuple[BattleEngine, BattleState]:
    eng = BattleEngine()
    bs = eng.create_battle(allies=[_char("hero"), _char("npc")], enemies=[_char("goblin")])
    return eng, bs


def _advance_to_hero(eng: BattleEngine, bs: BattleState) -> CombatantID:
    """턴을 hero까지 진행시켜 hero가 current actor가 되게 함."""
    while str(bs.current_actor_id()) != "hero":
        eng.end_turn(bs)
    return bs.current_actor_id()


def _heal_use_skill_def() -> UseSkillDef:
    return UseSkillDef(
        skill_id="USE_HEAL",
        name="Heal",
        action_type="SUB",
        target_filter="SELF",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=10, range="ANY", area="SINGLE")],
    )


def _heal_item_def() -> ItemDef:
    return ItemDef(item_id="HEAL_POT", weight=1, use_skill=_heal_use_skill_def())


def _sword_item_def() -> ItemDef:
    """use_skill 없는 무기 아이템."""
    return ItemDef(item_id="IRON_SWORD", weight=4, weapon_type="SWORD")


# =============================================================================
# 1. get_skill_availability: consume_item_id 수량 체크
# =============================================================================

def test_skill_availability_no_item_stock() -> None:
    """consume_item_id 아이템이 인벤토리에 없으면 NO_ITEM_STOCK."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {}  # 빈 인벤토리

    usd = _heal_use_skill_def()
    sk = build_item_use_skill(actor=actor, item_id="HEAL_POT", use_skill_def=usd)
    sk = replace(sk, steps=[replace(sk.steps[0], target=actor)])

    av = get_skill_availability(bs, sk)
    assert av.usable is False
    assert av.reason == "NO_ITEM_STOCK"


def test_skill_availability_with_item_stock() -> None:
    """consume_item_id 아이템 수량 충분하면 OK."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"HEAL_POT": 2}

    usd = _heal_use_skill_def()
    sk = build_item_use_skill(actor=actor, item_id="HEAL_POT", use_skill_def=usd)
    sk = replace(sk, steps=[replace(sk.steps[0], target=actor)])

    av = get_skill_availability(bs, sk)
    assert av.usable is True
    assert av.reason == "OK"


def test_skill_availability_no_consume_item_id_is_ok() -> None:
    """consume_item_id가 없는 일반 스킬은 아이템 체크 안 함."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    other = CombatantID("goblin")

    sk = Skill(
        skill_id="BASIC_ATTACK",
        name="Basic Attack",
        actor=actor,
        action_type="MAIN",
        target_filter="ENEMY",
        steps=[Step(kind="ATTACK", target=other, range="ANY", area="SINGLE")],
    )
    av = get_skill_availability(bs, sk)
    assert av.usable is True


# =============================================================================
# 2. list_use_items
# =============================================================================

def test_list_use_items_with_and_without_use_skill() -> None:
    """use_skill 있는 아이템은 enabled, 없는 아이템은 NO_USE_SKILL."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"HEAL_POT": 2, "IRON_SWORD": 1}

    registry = {
        "HEAL_POT": _heal_item_def(),
        "IRON_SWORD": _sword_item_def(),
    }

    opts = list_use_items(bs, actor, registry)
    by_id = {o.item_id: o for o in opts}

    assert by_id["HEAL_POT"].enabled is True
    assert by_id["HEAL_POT"].reason == "OK"
    assert by_id["HEAL_POT"].quantity == 2

    assert by_id["IRON_SWORD"].enabled is False
    assert by_id["IRON_SWORD"].reason == "NO_USE_SKILL"


def test_list_use_items_zero_stock() -> None:
    """수량 0인 아이템은 NO_STOCK."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"HEAL_POT": 0}

    registry = {"HEAL_POT": _heal_item_def()}
    opts = list_use_items(bs, actor, registry)
    assert opts[0].enabled is False
    assert opts[0].reason == "NO_STOCK"


def test_list_use_items_empty_inventory() -> None:
    """빈 인벤토리면 빈 리스트."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {}

    opts = list_use_items(bs, actor, {})
    assert opts == []


# =============================================================================
# 3. list_throw_items
# =============================================================================

def test_list_throw_items_with_stock() -> None:
    """수량 > 0인 아이템은 enabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"ROCK": 3, "HEAL_POT": 1}

    opts = list_throw_items(bs, actor)
    assert all(o.enabled for o in opts)
    assert all(o.reason == "OK" for o in opts)


def test_list_throw_items_zero_stock() -> None:
    """수량 0인 아이템은 NO_STOCK."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)
    bs.inventory_snapshot[actor] = {"ROCK": 0}

    opts = list_throw_items(bs, actor)
    assert opts[0].enabled is False
    assert opts[0].reason == "NO_STOCK"


# =============================================================================
# 4. list_target_options
# =============================================================================

def test_target_options_attack_enemy_filter() -> None:
    """ENEMY 필터 스킬: 적만 enabled, 아군은 WRONG_TEAM."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)

    sk = Skill(
        skill_id="SLASH",
        name="Slash",
        actor=actor,
        action_type="MAIN",
        target_filter="ENEMY",
        steps=[Step(kind="ATTACK", target=None, range="ANY", area="SINGLE")],
    )

    opts = list_target_options(bs, actor, sk)
    by_id = {str(o.target_id): o for o in opts}

    # goblin은 적이므로 enabled
    assert by_id["goblin"].enabled is True

    # 아군 npc는 WRONG_TEAM
    if "npc" in by_id:
        assert by_id["npc"].enabled is False
        assert by_id["npc"].reason == "WRONG_TEAM"

    # actor(hero) 자신은 ENEMY 필터에서 리스트에 안 나옴
    assert str(actor) not in by_id


def test_target_options_self_filter() -> None:
    """SELF 필터 스킬: actor만 enabled, 나머지 WRONG_TEAM."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)

    sk = Skill(
        skill_id="HEAL",
        name="Heal",
        actor=actor,
        action_type="SUB",
        target_filter="SELF",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=10, target=None, range="ANY", area="SINGLE")],
    )

    opts = list_target_options(bs, actor, sk)
    by_id = {str(o.target_id): o for o in opts}

    assert by_id[str(actor)].enabled is True
    # 다른 캐릭터는 WRONG_TEAM
    for o in opts:
        if o.target_id != actor:
            assert o.enabled is False
            assert o.reason == "WRONG_TEAM"


def test_target_options_ally_filter() -> None:
    """ALLY 필터 스킬: 같은 team만 enabled."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)

    sk = Skill(
        skill_id="BUFF",
        name="Buff",
        actor=actor,
        action_type="SUB",
        target_filter="ALLY",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=5, target=None, range="ANY", area="SINGLE")],
    )

    opts = list_target_options(bs, actor, sk)
    by_id = {str(o.target_id): o for o in opts}

    # hero, npc는 아군 → enabled
    assert by_id[str(actor)].enabled is True
    assert by_id["npc"].enabled is True

    # goblin은 적 → WRONG_TEAM
    assert by_id["goblin"].enabled is False
    assert by_id["goblin"].reason == "WRONG_TEAM"


def test_target_options_down_target() -> None:
    """DOWN 상태인 대상은 enabled=False, reason=DOWN."""
    eng, bs = _mk_battle_2v1()
    actor = _advance_to_hero(eng, bs)

    # goblin을 DOWN 상태로 만듦
    bs.combatants[CombatantID("goblin")].hp = 0

    sk = Skill(
        skill_id="SLASH",
        name="Slash",
        actor=actor,
        action_type="MAIN",
        target_filter="ENEMY",
        steps=[Step(kind="ATTACK", target=None, range="ANY", area="SINGLE")],
    )

    opts = list_target_options(bs, actor, sk)
    by_id = {str(o.target_id): o for o in opts}

    assert by_id["goblin"].enabled is False
    assert by_id["goblin"].reason == "DOWN"
