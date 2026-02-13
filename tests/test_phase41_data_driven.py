"""
Phase 41 — 데이터 기반 통합 테스트

Registry로 game_data 로드 → BattleSetup → 전투 기능 검증:
1. Registry 로드 성공
2. BattleSetup 필드 검증 (allies, enemies, skills, items, inventory)
3. 전투 생성 → 기본 공격 실행
4. 개인 스킬 실행 (Power Strike)
5. 아이템 사용 (Heal Potion)
6. 출혈 아이템 사용 (Bleed Vial)
7. 투척 (Throwing Stone)
8. 메뉴 트리 구성 검증
9. NPC 힐 스킬 실행
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState
from battle_system.core.commands import Skill, Step
from battle_system.engine.engine import BattleEngine
from battle_system.app.registry import Registry, BattleSetup
from battle_system.app.menu_builder import build_turn_menu
from battle_system.app.skill_ui import (
    get_skill_availability,
    instantiate_skill_with_inputs,
    build_item_use_skill,
)


# =============================================================================
# Fixture
# =============================================================================

GAME_DATA = Path(__file__).resolve().parent.parent.parent / "game_data"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry(GAME_DATA)


@pytest.fixture(scope="module")
def setup(registry: Registry) -> BattleSetup:
    return registry.build_battle_setup(
        allies=["HERO", "NPC_HEALER"],
        enemies=["GOBLIN_A", "GOBLIN_B", "GOBLIN_SHAMAN"],
    )


@pytest.fixture
def battle(setup: BattleSetup) -> tuple[BattleEngine, BattleState]:
    eng = BattleEngine()
    bs = eng.create_battle(allies=setup.allies, enemies=setup.enemies)
    # 인벤토리 로드
    for cid, inv in setup.initial_inventory.items():
        bs.inventory_snapshot[cid] = dict(inv)
    # 아이템 레지스트리 로드
    for iid, idef in setup.items.items():
        bs.items[iid] = idef
    return eng, bs


def _advance_to(eng: BattleEngine, bs: BattleState, name: str) -> CombatantID:
    """name이 current_actor가 될 때까지 end_turn."""
    limit = 100
    while str(bs.current_actor_id()) != name and limit > 0:
        eng.end_turn(bs)
        limit -= 1
    assert str(bs.current_actor_id()) == name, f"Could not advance to {name}"
    return bs.current_actor_id()


# =============================================================================
# 1. Registry 로드 성공
# =============================================================================

def test_registry_loads(registry: Registry) -> None:
    assert len(registry.items) == 5
    assert "IRON_SWORD" in registry.items
    assert "HEAL_POTION" in registry.items
    assert "THROWING_STONE" in registry.items
    assert "BLEED_VIAL" in registry.items
    assert "OAK_STAFF" in registry.items


# =============================================================================
# 2. BattleSetup 필드 검증
# =============================================================================

def test_battle_setup_structure(setup: BattleSetup) -> None:
    assert len(setup.allies) == 2
    assert len(setup.enemies) == 3
    assert setup.allies[0].cid == "HERO"
    assert setup.allies[1].cid == "NPC_HEALER"
    assert {e.cid for e in setup.enemies} == {"GOBLIN_A", "GOBLIN_B", "GOBLIN_SHAMAN"}


def test_skills_loaded(setup: BattleSetup) -> None:
    hero_skills = setup.skills_by_actor["HERO"]
    # basic_attack + engage + disengage + escape + 2 personal = 6
    skill_ids = [s.skill_id for s in hero_skills]
    assert "BASIC_ATTACK" in skill_ids
    assert "ENGAGE" in skill_ids
    assert "DISENGAGE" in skill_ids
    assert "ESCAPE" in skill_ids
    assert "SK_POWER_STRIKE" in skill_ids
    assert "SK_STEALTH_STRIKE" in skill_ids


def test_npc_skills_loaded(setup: BattleSetup) -> None:
    npc_skills = setup.skills_by_actor["NPC_HEALER"]
    skill_ids = [s.skill_id for s in npc_skills]
    assert "SK_HEAL_LIGHT" in skill_ids
    assert "SK_PURIFY" in skill_ids


def test_monster_skills_loaded(setup: BattleSetup) -> None:
    shaman_skills = setup.skills_by_actor["GOBLIN_SHAMAN"]
    skill_ids = [s.skill_id for s in shaman_skills]
    assert "SK_CURSE_BOLT" in skill_ids
    assert "SK_WEAKENING_HEX" in skill_ids


def test_inventories_loaded(setup: BattleSetup) -> None:
    assert setup.initial_inventory["HERO"]["HEAL_POTION"] == 2
    assert setup.initial_inventory["HERO"]["THROWING_STONE"] == 2
    assert setup.initial_inventory["HERO"]["BLEED_VIAL"] == 1
    assert setup.initial_inventory["NPC_HEALER"]["HEAL_POTION"] == 1


# =============================================================================
# 3. 전투 생성 → 기본 공격 실행
# =============================================================================

def test_basic_attack_execution(setup: BattleSetup, battle: tuple) -> None:
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    atk = [s for s in setup.skills_by_actor["HERO"] if s.skill_id == "BASIC_ATTACK"][0]
    goblin = CombatantID("GOBLIN_A")
    initial_hp = bs.combatants[goblin].hp

    concrete = instantiate_skill_with_inputs(atk, target=goblin)
    outcome = eng.apply_skill(bs, concrete)

    assert len(outcome.events) > 0
    assert bs.combatants[goblin].hp <= initial_hp


# =============================================================================
# 4. 개인 스킬 (Power Strike) 실행
# =============================================================================

def test_power_strike_no_target_before_engage(setup: BattleSetup, battle: tuple) -> None:
    """MELEE ENEMY 스킬은 같은 그룹에 적이 없으면 NO_VALID_TARGET."""
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    ps = [s for s in setup.skills_by_actor["HERO"] if s.skill_id == "SK_POWER_STRIKE"][0]
    av = get_skill_availability(bs, ps)
    assert av.usable is False
    assert av.reason == "NO_VALID_TARGET"


def test_power_strike_after_engage(setup: BattleSetup, battle: tuple) -> None:
    """Engage 후 같은 그룹으로 이동하면 MELEE 대상이 생긴다."""
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    # 고블린을 HERO와 같은 그룹에 배치 (engage 시뮬레이션)
    goblin = CombatantID("GOBLIN_A")
    hero_gid = bs.combatants[actor].group_id
    bs.combatants[goblin].group_id = hero_gid

    ps = [s for s in setup.skills_by_actor["HERO"] if s.skill_id == "SK_POWER_STRIKE"][0]
    av = get_skill_availability(bs, ps)
    assert av.usable is True

    concrete = instantiate_skill_with_inputs(ps, target=goblin)
    outcome = eng.apply_skill(bs, concrete)
    assert len(outcome.events) > 0


# =============================================================================
# 5. 아이템 사용 (Heal Potion)
# =============================================================================

def test_heal_potion_use(setup: BattleSetup, battle: tuple) -> None:
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    bs.combatants[actor].hp = 50  # HP 낮춤
    assert bs.inventory_snapshot[actor].get("HEAL_POTION", 0) >= 1

    idef = setup.items["HEAL_POTION"]
    sk = build_item_use_skill(actor=actor, item_id="HEAL_POTION", use_skill_def=idef.use_skill)
    concrete = instantiate_skill_with_inputs(sk, target=actor)
    outcome = eng.apply_skill(bs, concrete)

    # HP 회복 (50 + 30 = 80)
    assert bs.combatants[actor].hp == 80
    # 아이템 소모
    assert bs.inventory_snapshot[actor]["HEAL_POTION"] == 1


# =============================================================================
# 6. 출혈 아이템 사용 (Bleed Vial)
# =============================================================================

def test_bleed_vial_use(setup: BattleSetup, battle: tuple) -> None:
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    assert bs.inventory_snapshot[actor].get("BLEED_VIAL", 0) >= 1

    idef = setup.items["BLEED_VIAL"]
    goblin = CombatantID("GOBLIN_B")
    sk = build_item_use_skill(actor=actor, item_id="BLEED_VIAL", use_skill_def=idef.use_skill)
    concrete = instantiate_skill_with_inputs(sk, target=goblin)
    outcome = eng.apply_skill(bs, concrete)

    # 아이템 소모 확인 (시도 시 소모이므로 저항해도 소모)
    assert bs.inventory_snapshot[actor].get("BLEED_VIAL", 0) == 0
    assert "ITEM_CONSUMED" in " ".join(outcome.events)


# =============================================================================
# 7. 투척 (Throwing Stone)
# =============================================================================

def test_throw_stone(setup: BattleSetup, battle: tuple) -> None:
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    # throw는 기본행동에 포함된 BASIC_ATTACK 계열이 아니라
    # TACTICAL_THROW step이 있는 스킬
    # menu_builder에서 throw 항목을 만들지만 실제 throw skill은 암묵적(스텝 기반)
    # 여기서는 수동 Skill 생성으로 투척 검증
    throw_sk = Skill(
        skill_id="THROW",
        name="Throw",
        actor=actor,
        action_type="MAIN",
        crit_stat="STR",
        target_filter="ENEMY",
        steps=[Step(
            kind="TACTICAL_THROW",
            target=CombatantID("GOBLIN_A"),
            range="RANGED",
            area="SINGLE",
            throw_item_id="THROWING_STONE",
        )],
    )

    initial_stones = bs.inventory_snapshot[actor].get("THROWING_STONE", 0)
    outcome = eng.apply_skill(bs, throw_sk)

    assert "THROW" in " ".join(outcome.events) or len(outcome.events) > 0
    assert bs.inventory_snapshot[actor].get("THROWING_STONE", 0) == initial_stones - 1


# =============================================================================
# 8. 메뉴 트리 구성 검증
# =============================================================================

def test_menu_tree_from_registry_data(setup: BattleSetup, battle: tuple) -> None:
    eng, bs = battle
    actor = _advance_to(eng, bs, "HERO")

    # Engage 시뮬레이션: GOBLIN_A를 HERO 같은 그룹으로 이동
    hero_gid = bs.combatants[actor].group_id
    bs.combatants[CombatantID("GOBLIN_A")].group_id = hero_gid

    menu = build_turn_menu(
        bs, actor,
        setup.skills_by_actor["HERO"],
        items_registry=setup.items,
    )

    # 4개 루트 노드
    assert len(menu.nodes) == 4
    basic = menu.get_node("BASIC")
    unique = menu.get_node("UNIQUE")
    item_use = menu.get_node("ITEM_USE")
    end_turn = menu.get_node("END_TURN")

    assert basic.enabled is True
    assert unique.enabled is True
    assert end_turn.enabled is True

    # 고유행동에 Power Strike과 Stealth Strike 있어야 함
    unique_ids = [i.label for i in unique.items]
    assert "강타" in unique_ids
    assert "은밀 일격" in unique_ids


# =============================================================================
# 9. NPC 힐 스킬 실행
# =============================================================================

def test_npc_heal_light(setup: BattleSetup, battle: tuple) -> None:
    eng, bs = battle
    actor = _advance_to(eng, bs, "NPC_HEALER")
    hero = CombatantID("HERO")

    bs.combatants[hero].hp = 30  # HP 낮춤

    heal = [s for s in setup.skills_by_actor["NPC_HEALER"] if s.skill_id == "SK_HEAL_LIGHT"][0]
    concrete = instantiate_skill_with_inputs(heal, target=hero)
    outcome = eng.apply_skill(bs, concrete)

    # HP 회복 (30 + 20 = 50)
    assert bs.combatants[hero].hp == 50


# =============================================================================
# 10. 무기 호환 필터 검증
# =============================================================================

def test_weapon_compatibility_filter(setup: BattleSetup) -> None:
    """HERO(SWORD)는 STAFF 전용 스킬을 가지면 안 됨"""
    hero_ids = {s.skill_id for s in setup.skills_by_actor["HERO"]}
    # SK_HEAL_LIGHT은 STAFF/ORB 전용 → HERO(SWORD)에서 제외
    assert "SK_HEAL_LIGHT" not in hero_ids
    # SK_POWER_STRIKE는 SWORD 포함 → HERO에 있어야 함
    assert "SK_POWER_STRIKE" in hero_ids
