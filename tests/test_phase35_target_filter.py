"""
Phase 35 — Target Filter 테스트

Skill.target_filter가 UI 후보 목록에 올바르게 반영되는지 검증.
- ANY: 모든 대상 포함
- SELF: 자기 자신만
- ALLY: 같은 team만 (자기 포함)
- ENEMY: 다른 team만
- 필터 결과 후보 0 → NO_VALID_TARGET
"""
from __future__ import annotations

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import CharacterDef, Stats
from battle_system.core.commands import Skill, Step

from battle_system.app.skill_ui import (
    get_skill_availability,
    compute_input_spec,
    list_usable_skills,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def mk_char(cid: str, *, level: int = 1, hp: int = 30) -> CharacterDef:
    return CharacterDef(
        cid=cid,
        name=cid,
        level=level,
        stats=Stats(10, 10, 10, 10, 10, 10),
        max_hp=hp,
    )


def mk_battle_2v2() -> tuple[BattleEngine, object]:
    """
    2v2 전투 생성: A1, A2 (ALLY) vs E1, E2 (ENEMY)
    """
    engine = BattleEngine()
    a1 = mk_char("A1")
    a2 = mk_char("A2")
    e1 = mk_char("E1")
    e2 = mk_char("E2")
    bs = engine.create_battle(allies=[a1, a2], enemies=[e1, e2])
    return engine, bs


def mk_battle_1v1() -> tuple[BattleEngine, object]:
    engine = BattleEngine()
    a = mk_char("A")
    b = mk_char("B")
    bs = engine.create_battle(allies=[a], enemies=[b])
    return engine, bs


def mk_skill_with_filter(
    actor: str,
    *,
    target_filter: str = "ANY",
    kind: str = "APPLY_HP_DELTA",
    range_: str = "ANY",
    area: str = "SINGLE",
    action_type: str = "MAIN",
    skill_id: str = "S_FILTERED",
    hp_delta: int = 5,
) -> Skill:
    """target_filter를 지정할 수 있는 스킬 생성 헬퍼."""
    return Skill(
        skill_id=skill_id,
        name="FilteredSkill",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        steps=[Step(kind=kind, target=None, range=range_, area=area, hp_delta=hp_delta)],
        crit_stat="STR",  # type: ignore[arg-type]
        target_filter=target_filter,  # type: ignore[arg-type]
    )


# -----------------------------------------------------------------------------
# target_filter=ANY (기본값) — 기존 동작과 동일
# -----------------------------------------------------------------------------

def test_target_filter_any_includes_all_others() -> None:
    """target_filter=ANY이면 자기 자신 제외, 모든 살아있는 대상이 후보에 포함되어야 한다."""
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()

    sk = mk_skill_with_filter(actor, target_filter="ANY")
    av = get_skill_availability(bs, sk)

    assert av.usable is True
    assert av.reason == "OK"
    # 자기 자신은 제외, 나머지 3명이 후보
    assert actor not in av.spec.target_candidates
    assert len(av.spec.target_candidates) == 3


# -----------------------------------------------------------------------------
# target_filter=SELF — 자기 자신만
# -----------------------------------------------------------------------------

def test_target_filter_self_only_actor() -> None:
    """target_filter=SELF이면 자기 자신만 후보에 포함되어야 한다."""
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()

    sk = mk_skill_with_filter(actor, target_filter="SELF")
    av = get_skill_availability(bs, sk)

    assert av.usable is True
    assert av.reason == "OK"
    assert av.spec.target_candidates == [actor]


def test_target_filter_self_heal_usable_and_correct() -> None:
    """SELF 힐 스킬: HP 델타가 양수이며 대상이 자기 자신뿐인 완전한 시나리오."""
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()

    # HP를 좀 깎아놓고
    bs.combatants[actor].hp = 10

    sk = mk_skill_with_filter(actor, target_filter="SELF", hp_delta=5, skill_id="S_SELF_HEAL")
    av = get_skill_availability(bs, sk)

    assert av.usable is True
    assert av.spec.target_required is True
    assert av.spec.target_candidates == [actor]


# -----------------------------------------------------------------------------
# target_filter=ALLY — 같은 team만
# -----------------------------------------------------------------------------

def test_target_filter_ally_only_same_team() -> None:
    """target_filter=ALLY이면 actor와 같은 team(ALLY)인 대상만 후보에 포함되어야 한다."""
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()
    actor_team = bs.combatants[actor].team

    sk = mk_skill_with_filter(actor, target_filter="ALLY", skill_id="S_ALLY_HEAL")
    av = get_skill_availability(bs, sk)

    assert av.usable is True
    assert av.reason == "OK"

    # 후보는 모두 actor와 같은 team
    for cid in av.spec.target_candidates:
        assert bs.combatants[cid].team == actor_team, f"{cid} should be same team as {actor}"

    # ALLY 필터에서 자기 자신도 후보에 포함
    assert actor in av.spec.target_candidates

    # 적 team은 후보에 없어야 한다
    for cid, st in bs.combatants.items():
        if st.team != actor_team:
            assert cid not in av.spec.target_candidates


# -----------------------------------------------------------------------------
# target_filter=ENEMY — 다른 team만
# -----------------------------------------------------------------------------

def test_target_filter_enemy_only_other_team() -> None:
    """target_filter=ENEMY이면 actor와 다른 team인 대상만 후보에 포함되어야 한다."""
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()
    actor_team = bs.combatants[actor].team

    sk = mk_skill_with_filter(actor, target_filter="ENEMY", skill_id="S_ENEMY_ONLY")
    av = get_skill_availability(bs, sk)

    assert av.usable is True
    assert av.reason == "OK"

    # 후보는 모두 actor와 다른 team
    for cid in av.spec.target_candidates:
        assert bs.combatants[cid].team != actor_team, f"{cid} should be different team from {actor}"

    # 자기 자신은 후보에 없어야 한다
    assert actor not in av.spec.target_candidates


# -----------------------------------------------------------------------------
# 필터 후 후보 0 → NO_VALID_TARGET
# -----------------------------------------------------------------------------

def test_target_filter_no_valid_target_when_allies_all_down() -> None:
    """
    target_filter=ALLY에서 자기 외 아군이 모두 DOWN이면,
    자기 자신은 남지만 후보가 1명(SELF)이므로 usable은 True.
    하지만 SELF를 원치 않는다면 별도 스킬 설계가 필요.
    여기서는 자기 포함 ALLY 후보가 최소 1이므로 usable=True를 확인.
    """
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()
    actor_team = bs.combatants[actor].team

    # 같은 팀 다른 멤버를 모두 DOWN
    for cid, st in bs.combatants.items():
        if st.team == actor_team and cid != actor:
            st.hp = 0

    sk = mk_skill_with_filter(actor, target_filter="ALLY", skill_id="S_ALLY_ONLY2")
    av = get_skill_availability(bs, sk)

    # 자기 자신이 ALLY에 포함되므로 후보 1명은 있음
    assert av.usable is True
    assert av.spec.target_candidates == [actor]


def test_target_filter_no_valid_target_enemy_all_down() -> None:
    """target_filter=ENEMY에서 적이 모두 DOWN이면 NO_VALID_TARGET으로 스킬 불가."""
    _, bs = mk_battle_2v2()
    actor = bs.current_actor_id()
    actor_team = bs.combatants[actor].team

    # 적 팀 전원 DOWN
    for cid, st in bs.combatants.items():
        if st.team != actor_team:
            st.hp = 0

    sk = mk_skill_with_filter(actor, target_filter="ENEMY", skill_id="S_ENEMY_ONLY2")
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "NO_VALID_TARGET"


def test_target_filter_self_when_actor_is_down() -> None:
    """actor가 DOWN이면 SELF 스킬도 ACTOR_DOWN으로 사용 불가."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()

    bs.combatants[actor].hp = 0
    sk = mk_skill_with_filter(actor, target_filter="SELF", skill_id="S_SELF_DOWN")
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "ACTOR_DOWN"
