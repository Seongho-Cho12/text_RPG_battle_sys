from __future__ import annotations

import random
from typing import Tuple

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Skill, Step


# -------------------------
# helpers
# -------------------------

def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int = 50) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def _mk_engine_2v2() -> Tuple[BattleEngine, object, CombatantID, CombatantID, CombatantID, CombatantID]:
    """
    2v2 전투를 생성한다.
    - A1이 선턴이 되도록 AGI를 크게 준다.
    - create_battle 규칙에 따라 팀별로 한 그룹으로 시작한다.
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=10, stats=Stats(str=10, agi=30, con=10, int=10, wis=10, cha=10), max_hp=50)
    a2 = _mk_char("A2", level=10, stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10), max_hp=50)
    e1 = _mk_char("E1", level=10, stats=Stats(str=10, agi=8,  con=10, int=10, wis=10, cha=10), max_hp=50)
    e2 = _mk_char("E2", level=10, stats=Stats(str=10, agi=7,  con=10, int=10, wis=10, cha=10), max_hp=50)

    bs = eng.create_battle([a1, a2], [e1, e2])
    assert bs.current_actor_id() == CombatantID("A1")
    return eng, bs, CombatantID("A1"), CombatantID("A2"), CombatantID("E1"), CombatantID("E2")


def _gid(bs, cid: CombatantID):
    return bs.combatants[cid].group_id


def _force_move_to_group(bs, cid: CombatantID, new_gid):
    """
    테스트 셋업 편의용 강제 이동.
    - 이동/반응 로직 검증이 목적이 아니라, 사거리/범위만 검증하기 위한 배치 조정이다.
    """
    old_gid = bs.combatants[cid].group_id
    if old_gid == new_gid:
        return

    if old_gid in bs.groups and cid in bs.groups[old_gid]:
        bs.groups[old_gid].remove(cid)

    bs.groups.setdefault(new_gid, []).append(cid)
    bs.combatants[cid].group_id = new_gid


def _events(outcome) -> list[str]:
    """
    EngineOutcome에서 events를 꺼낸다.
    (필드명이 혹시 바뀌어도 에러 메시지를 명확히 하기 위한 헬퍼)
    """
    ev = getattr(outcome, "events", None)
    if ev is None:
        raise AttributeError("apply_skill return object has no '.events'")
    return ev


# -------------------------
# Phase 24 tests (range + area)
# -------------------------

def test_phase24_attack_melee_out_of_range_when_different_groups():
    """
    [Phase 24] 근거리(MELEE) 공격은 같은 그룹(=근접)일 때만 가능하다.

    시나리오:
    - 전투 시작 직후 A1(아군)과 E1(적군)은 서로 다른 그룹에 있다.
    - A1이 E1을 근거리 공격(range=MELEE)으로 시도하면 사거리 조건 불만족.
    기대:
    - OUT_OF_RANGE 이벤트가 남고, 공격이 실제로 실행되지 않는다(ATTACK 로그 없음).
    - E1의 HP는 변하지 않는다.
    """
    random.seed(1234)
    eng, bs, A1, _, E1, _ = _mk_engine_2v2()
    before = bs.combatants[E1].hp

    skill = Skill(
        skill_id="P24_MELEE_OOR",
        name="melee out of range",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(kind="ATTACK", target=E1, range="MELEE", area="SINGLE"),
        ],
    )

    out = eng.apply_skill(bs, skill)
    events = _events(out)

    assert any(e.startswith("OUT_OF_RANGE: ATTACK") for e in events)
    assert not any(e.startswith("STEP: ATTACK") for e in events)
    assert bs.combatants[E1].hp == before


def test_phase24_attack_ranged_out_of_range_when_same_group():
    """
    [Phase 24] 원거리(RANGED) 공격은 다른 그룹(=원거리)일 때만 가능하다.

    시나리오:
    - 강제로 E1을 A1과 같은 그룹으로 옮겨 '근접 상태'를 만든다.
    - A1이 E1을 원거리 공격(range=RANGED)으로 시도하면 사거리 조건 불만족.
    기대:
    - OUT_OF_RANGE 이벤트가 남고, 공격이 실제로 실행되지 않는다(ATTACK 로그 없음).
    """
    random.seed(1234)
    eng, bs, A1, _, E1, _ = _mk_engine_2v2()

    _force_move_to_group(bs, E1, _gid(bs, A1))

    skill = Skill(
        skill_id="P24_RANGED_OOR",
        name="ranged out of range",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(kind="ATTACK", target=E1, range="RANGED", area="SINGLE"),
        ],
    )

    out = eng.apply_skill(bs, skill)
    events = _events(out)

    assert any(e.startswith("OUT_OF_RANGE: ATTACK") for e in events)
    assert not any(e.startswith("STEP: ATTACK") for e in events)


def test_phase24_area_group_applies_only_to_anchor_team_even_if_group_mixed():
    """
    [Phase 24] 범위(GROUP)는 '타겟(앵커)이 속한 그룹'에 적용되지만,
              그룹 안에 팀이 섞여 있어도 '앵커와 같은 팀'에게만 적용되어야 한다.

    시나리오:
    - 적 그룹(enemy_group)에 아군 A2를 강제로 넣어 혼합 그룹을 만든다.
    - A1이 target=E1, area=GROUP로 HP_DELTA(-5)를 시전한다.
    기대:
    - E1, E2(ENEMY)만 HP가 줄어든다.
    - A2(ALLY)는 영향을 받지 않는다.
    """
    eng, bs, A1, A2, E1, E2 = _mk_engine_2v2()

    enemy_gid = _gid(bs, E1)
    _force_move_to_group(bs, A2, enemy_gid)

    e1_before = bs.combatants[E1].hp
    e2_before = bs.combatants[E2].hp
    a2_before = bs.combatants[A2].hp

    skill = Skill(
        skill_id="P24_GROUP_TEAM_FILTER",
        name="group area team filter",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(kind="APPLY_HP_DELTA", target=E1, hp_delta=-5, range="ANY", area="GROUP"),
        ],
    )

    out = eng.apply_skill(bs, skill)
    events = _events(out)

    assert any(e.startswith(f"HP_DELTA: {E1}") for e in events)
    assert any(e.startswith(f"HP_DELTA: {E2}") for e in events)
    assert not any(e.startswith(f"HP_DELTA: {A2}") for e in events)

    assert bs.combatants[E1].hp == e1_before - 5
    assert bs.combatants[E2].hp == e2_before - 5
    assert bs.combatants[A2].hp == a2_before


def test_phase24_area_all_applies_to_everyone_without_target():
    """
    [Phase 24] 광범위(ALL)는 전투 참가 전체에게 적용되어야 하며,
              이 경우 target=None 이어도 동작해야 한다.

    시나리오:
    - A1이 area=ALL, target=None 인 HP_DELTA(+1)를 사용한다.
    기대:
    - A1/A2/E1/E2 모두 HP가 +1 된다.
    - 이벤트 로그에 HP_DELTA가 4개 찍힌다.
    """
    eng, bs, A1, A2, E1, E2 = _mk_engine_2v2()

    before = {cid: bs.combatants[cid].hp for cid in [A1, A2, E1, E2]}

    skill = Skill(
        skill_id="P24_ALL_NO_TARGET",
        name="all area without target",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(kind="APPLY_HP_DELTA", target=None, hp_delta=-1, range="ANY", area="ALL"),
        ],
    )

    out = eng.apply_skill(bs, skill)
    events = _events(out)

    hp_logs = [e for e in events if e.startswith("HP_DELTA:")]
    assert len(hp_logs) == 4

    for cid in [A1, A2, E1, E2]:
        assert bs.combatants[cid].hp == before[cid] - 1


def test_phase24_apply_effect_group_expands_targets():
    """
    [Phase 24] APPLY_EFFECT도 범위(GROUP)를 통해 여러 대상에게 확장 적용되어야 한다.

    시나리오:
    - A1이 E1을 앵커로 하여 area=GROUP 상태이상 부여를 시전.
    - INSTANT_DEATH는 resistible=False라 roll 없이 자동 성공한다.
    기대:
    - E1과 E2(적군 그룹 전원)에게 effect가 부여된다.
    - STATUS_CHECK / EFFECT_APPLIED 로그가 2명에 대해 남는다.
    """
    eng, bs, A1, _, E1, E2 = _mk_engine_2v2()

    skill = Skill(
        skill_id="P24_EFFECT_GROUP",
        name="apply effect group",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=E1,
                effect_id="INSTANT_DEATH",
                effect_duration=1,
                status_inflict=999,
                range="ANY",
                area="GROUP",
            ),
        ],
    )

    out = eng.apply_skill(bs, skill)
    events = _events(out)

    assert any(e.startswith(f"STATUS_CHECK: {A1}->{E1} effect=INSTANT_DEATH") for e in events)
    assert any(e.startswith(f"STATUS_CHECK: {A1}->{E2} effect=INSTANT_DEATH") for e in events)

    assert "INSTANT_DEATH" in bs.combatants[E1].effects
    assert "INSTANT_DEATH" in bs.combatants[E2].effects
