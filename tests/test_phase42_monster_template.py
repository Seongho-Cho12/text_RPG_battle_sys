"""
Phase 42 — 몬스터 템플릿 분리 테스트

1. 인스턴스 ID 생성 규칙
2. 같은 종 2마리 소환: 독립 CID, 독립 HP
3. 캐릭터는 여전히 characters/에서 로드
4. 스킬 actor 필드가 인스턴스 ID로 세팅
5. 혼합 시나리오: 같은 종 2 + 다른 종 1
"""
from __future__ import annotations

import pytest
from pathlib import Path

from battle_system.core.types import CombatantID
from battle_system.engine.engine import BattleEngine
from battle_system.app.registry import Registry, BattleSetup, _make_instance_ids


GAME_DATA = Path(__file__).resolve().parent.parent.parent / "game_data"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry(GAME_DATA)


# =============================================================================
# 1. _make_instance_ids 단위 테스트
# =============================================================================

def test_instance_ids_single_each() -> None:
    """각 템플릿이 1개뿐이면 bare ID 그대로."""
    result = _make_instance_ids(["GOBLIN_A", "GOBLIN_B"])
    assert result == [("GOBLIN_A", "GOBLIN_A"), ("GOBLIN_B", "GOBLIN_B")]


def test_instance_ids_duplicates() -> None:
    """같은 템플릿 2개 이상이면 #1, #2 ... 붙인다."""
    result = _make_instance_ids(["GOBLIN_A", "GOBLIN_A"])
    assert result == [("GOBLIN_A#1", "GOBLIN_A"), ("GOBLIN_A#2", "GOBLIN_A")]


def test_instance_ids_mixed() -> None:
    """중복 + 단일 혼합."""
    result = _make_instance_ids(["GOBLIN_A", "GOBLIN_B", "GOBLIN_A"])
    assert result == [
        ("GOBLIN_A#1", "GOBLIN_A"),
        ("GOBLIN_B", "GOBLIN_B"),
        ("GOBLIN_A#2", "GOBLIN_A"),
    ]


def test_instance_ids_triple() -> None:
    """같은 템플릿 3개."""
    result = _make_instance_ids(["X", "X", "X"])
    assert result == [("X#1", "X"), ("X#2", "X"), ("X#3", "X")]


# =============================================================================
# 2. 같은 종 2마리 소환
# =============================================================================

def test_spawn_two_of_same_monster(registry: Registry) -> None:
    setup = registry.build_battle_setup(
        allies=["HERO"],
        enemies=["GOBLIN_A", "GOBLIN_A"],
    )

    # CID 검증
    enemy_cids = [e.cid for e in setup.enemies]
    assert enemy_cids == ["GOBLIN_A#1", "GOBLIN_A#2"]

    # 독립 HP
    eng = BattleEngine()
    bs = eng.create_battle(allies=setup.allies, enemies=setup.enemies)
    assert bs.combatants["GOBLIN_A#1"].hp == 40
    assert bs.combatants["GOBLIN_A#2"].hp == 40

    # HP 독립성: 하나를 깎아도 다른 것에 영향 없음
    bs.combatants["GOBLIN_A#1"].hp = 10
    assert bs.combatants["GOBLIN_A#2"].hp == 40


# =============================================================================
# 3. 캐릭터는 characters/에서 정상 로드
# =============================================================================

def test_characters_still_from_characters_dir(registry: Registry) -> None:
    setup = registry.build_battle_setup(
        allies=["HERO", "NPC_HEALER"],
        enemies=["GOBLIN_A"],
    )
    assert setup.allies[0].cid == "HERO"
    assert setup.allies[1].cid == "NPC_HEALER"
    assert setup.allies[0].max_hp == 80
    assert setup.allies[1].max_hp == 60


# =============================================================================
# 4. 스킬 actor 필드가 인스턴스 ID
# =============================================================================

def test_skills_use_instance_cid(registry: Registry) -> None:
    setup = registry.build_battle_setup(
        allies=["HERO"],
        enemies=["GOBLIN_A", "GOBLIN_A"],
    )

    for sk in setup.skills_by_actor["GOBLIN_A#1"]:
        assert sk.actor == "GOBLIN_A#1"

    for sk in setup.skills_by_actor["GOBLIN_A#2"]:
        assert sk.actor == "GOBLIN_A#2"


# =============================================================================
# 5. 혼합 시나리오
# =============================================================================

def test_mixed_enemies(registry: Registry) -> None:
    """같은 종 2 + 다른 종 1."""
    setup = registry.build_battle_setup(
        allies=["HERO"],
        enemies=["GOBLIN_A", "GOBLIN_SHAMAN", "GOBLIN_A"],
    )

    enemy_cids = [e.cid for e in setup.enemies]
    assert enemy_cids == ["GOBLIN_A#1", "GOBLIN_SHAMAN", "GOBLIN_A#2"]

    # 각각의 스킬이 올바른 actor를 가지는지
    assert "GOBLIN_A#1" in setup.skills_by_actor
    assert "GOBLIN_SHAMAN" in setup.skills_by_actor
    assert "GOBLIN_A#2" in setup.skills_by_actor

    # GOBLIN_SHAMAN에 고유스킬 확인
    shaman_ids = [s.skill_id for s in setup.skills_by_actor["GOBLIN_SHAMAN"]]
    assert "SK_CURSE_BOLT" in shaman_ids
    assert "SK_WEAKENING_HEX" in shaman_ids


# =============================================================================
# 6. 단일 몬스터는 bare ID
# =============================================================================

def test_single_monster_bare_id(registry: Registry) -> None:
    setup = registry.build_battle_setup(
        allies=["HERO"],
        enemies=["GOBLIN_B"],
    )
    assert setup.enemies[0].cid == "GOBLIN_B"
    assert "GOBLIN_B" in setup.skills_by_actor
