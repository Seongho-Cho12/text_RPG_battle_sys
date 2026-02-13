"""
Phase 37 — 아이템 사용 스킬 인스턴스화 + 시도 시 소모 테스트

1. build_item_use_skill: UseSkillDef → Skill 변환 정확성
2. engine.apply_skill: consume_item_id 설정 시 아이템 소모
3. 아이템 부족 시 ValueError
4. consume_item_id 없으면 소모 안 함
5. inventory_delta 반영 확인
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
from battle_system.app.skill_ui import build_item_use_skill


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


def _use_skill_def_heal() -> UseSkillDef:
    """힐링 물약 UseSkillDef"""
    return UseSkillDef(
        skill_id="USE_HEALING_POTION",
        name="Healing Potion",
        action_type="SUB",
        target_filter="SELF",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=10, range="ANY", area="SINGLE")],
    )


# =============================================================================
# 1. build_item_use_skill 변환 정확성
# =============================================================================

def test_build_item_use_skill_basic_fields() -> None:
    """UseSkillDef → Skill 변환 시 필드가 정확히 매핑되는지 확인."""
    usd = _use_skill_def_heal()
    sk = build_item_use_skill(actor=CombatantID("hero"), item_id="HEALING_POTION", use_skill_def=usd)

    assert sk.skill_id == "USE:HEALING_POTION"
    assert sk.name == "Healing Potion"
    assert sk.actor == CombatantID("hero")
    assert sk.action_type == "SUB"
    assert sk.target_filter == "SELF"
    assert sk.consume_item_id == "HEALING_POTION"
    assert len(sk.steps) == 1
    assert sk.steps[0].kind == "APPLY_HP_DELTA"
    assert sk.steps[0].hp_delta == 10


def test_build_item_use_skill_preserves_defaults() -> None:
    """UseSkillDef 기본값이 Skill로 정확히 전달되는지 확인."""
    usd = UseSkillDef(
        skill_id="USE_ITEM",
        name="Use Item",
        steps=[Step(kind="APPLY_HP_DELTA", hp_delta=5)],
    )
    sk = build_item_use_skill(actor=CombatantID("hero"), item_id="HERB", use_skill_def=usd)

    assert sk.action_type == "SUB"       # UseSkillDef 기본값
    assert sk.cooldown_turns == 0
    assert sk.crit_stat == "STR"
    assert sk.target_filter == "ANY"
    assert sk.consume_item_id == "HERB"


# =============================================================================
# 2. 엔진 apply_skill: 아이템 소모 확인
# =============================================================================

def test_engine_consumes_item_on_apply_skill() -> None:
    """consume_item_id가 설정된 Skill 실행 시 인벤토리에서 1개 소모되어야 한다."""
    eng, bs = _mk_battle_1v1()
    actor = bs.current_actor_id()

    # 인벤토리 설정
    bs.inventory_snapshot[actor] = {"HEALING_POTION": 3}
    bs.items["HEALING_POTION"] = ItemDef(item_id="HEALING_POTION", weight=1)

    # 힐링 물약 스킬 (target=자기자신)
    usd = _use_skill_def_heal()
    sk = build_item_use_skill(actor=actor, item_id="HEALING_POTION", use_skill_def=usd)
    sk = replace(sk, steps=[replace(sk.steps[0], target=actor)])

    outcome = eng.apply_skill(bs, sk)

    # 소모 확인
    assert bs.inventory_snapshot[actor].get("HEALING_POTION", 0) == 2
    assert "ITEM_CONSUMED" in " ".join(outcome.events)

    # delta 확인 (-1)
    assert bs.inventory_delta[actor]["HEALING_POTION"] == -1


def test_engine_consumes_multiple_items_sequentially() -> None:
    """연속 사용 시 인벤토리가 올바르게 감소하고 delta가 누적되어야 한다."""
    eng, bs = _mk_battle_1v1()
    actor = bs.current_actor_id()

    bs.inventory_snapshot[actor] = {"HEALING_POTION": 2}
    bs.items["HEALING_POTION"] = ItemDef(item_id="HEALING_POTION", weight=1)

    usd = _use_skill_def_heal()

    # 1차 사용 (SUB 슬롯)
    sk1 = build_item_use_skill(actor=actor, item_id="HEALING_POTION", use_skill_def=usd)
    sk1 = replace(sk1, steps=[replace(sk1.steps[0], target=actor)])
    eng.apply_skill(bs, sk1)

    assert bs.inventory_snapshot[actor]["HEALING_POTION"] == 1

    # 턴 종료 후 다시 자기 턴이 올 때까지 진행
    eng.end_turn(bs)  # goblin turn
    eng.end_turn(bs)  # hero turn again

    # 2차 사용
    sk2 = build_item_use_skill(actor=actor, item_id="HEALING_POTION", use_skill_def=usd)
    sk2 = replace(sk2, steps=[replace(sk2.steps[0], target=actor)])
    eng.apply_skill(bs, sk2)

    assert bs.inventory_snapshot[actor].get("HEALING_POTION", 0) == 0
    assert bs.inventory_delta[actor]["HEALING_POTION"] == -2


# =============================================================================
# 3. 아이템 부족 시 ValueError
# =============================================================================

def test_engine_raises_on_insufficient_item() -> None:
    """인벤토리에 아이템이 없으면 ValueError가 발생해야 한다."""
    eng, bs = _mk_battle_1v1()
    actor = bs.current_actor_id()

    bs.inventory_snapshot[actor] = {}  # 빈 인벤토리

    usd = _use_skill_def_heal()
    sk = build_item_use_skill(actor=actor, item_id="HEALING_POTION", use_skill_def=usd)
    sk = replace(sk, steps=[replace(sk.steps[0], target=actor)])

    with pytest.raises(ValueError, match="insufficient quantity"):
        eng.apply_skill(bs, sk)


# =============================================================================
# 4. consume_item_id 없으면 소모 안 함
# =============================================================================

def test_engine_does_not_consume_without_consume_item_id() -> None:
    """consume_item_id가 None인 일반 스킬은 아이템을 소모하지 않아야 한다."""
    eng, bs = _mk_battle_1v1()
    actor = bs.current_actor_id()

    bs.inventory_snapshot[actor] = {"HEALING_POTION": 3}

    # 일반 공격 스킬 (consume_item_id 없음)
    target = [cid for cid in bs.combatants if cid != actor][0]
    sk = Skill(
        skill_id="BASIC_ATTACK",
        name="Basic Attack",
        actor=actor,
        action_type="MAIN",
        steps=[Step(kind="ATTACK", target=target, range="ANY", area="SINGLE")],
    )
    eng.apply_skill(bs, sk)

    assert bs.inventory_snapshot[actor]["HEALING_POTION"] == 3
    assert actor not in bs.inventory_delta or "HEALING_POTION" not in bs.inventory_delta.get(actor, {})


# =============================================================================
# 5. HP_DELTA 실제 적용 + 소모 동시 확인
# =============================================================================

def test_item_use_heals_and_consumes() -> None:
    """아이템 사용 시 HP 회복과 아이템 소모가 모두 정상 적용되어야 한다."""
    eng, bs = _mk_battle_1v1()
    actor = bs.current_actor_id()

    # HP를 낮춤
    bs.combatants[actor].hp = 50

    bs.inventory_snapshot[actor] = {"HEALING_POTION": 1}
    bs.items["HEALING_POTION"] = ItemDef(item_id="HEALING_POTION", weight=1)

    usd = _use_skill_def_heal()
    sk = build_item_use_skill(actor=actor, item_id="HEALING_POTION", use_skill_def=usd)
    sk = replace(sk, steps=[replace(sk.steps[0], target=actor)])

    eng.apply_skill(bs, sk)

    # HP 회복 확인 (50 + 10 = 60)
    assert bs.combatants[actor].hp == 60

    # 소모 확인
    assert bs.inventory_snapshot[actor].get("HEALING_POTION", 0) == 0
