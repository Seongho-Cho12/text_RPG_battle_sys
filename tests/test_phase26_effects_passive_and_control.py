from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Callable, Optional

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Skill, Step
from battle_system.rules.indices.facade import compute_attack_indices


# -------------------------
# helpers: battle creation
# -------------------------

def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int = 50) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def _mk_engine_1v1() -> Tuple[BattleEngine, object, CombatantID, CombatantID]:
    """
    1v1 전투 생성(테스트용).
    - A가 선턴이 되도록 AGI를 크게 준다.
    """
    eng = BattleEngine()
    a = _mk_char("A", level=10, stats=Stats(str=10, agi=30, con=10, int=10, wis=10, cha=10), max_hp=50)
    e = _mk_char("E", level=10, stats=Stats(str=10, agi=5,  con=10, int=10, wis=10, cha=10), max_hp=50)
    bs = eng.create_battle([a], [e])
    assert bs.current_actor_id() == CombatantID("A")
    return eng, bs, CombatantID("A"), CombatantID("E")


def _set_effect(bs, cid: CombatantID, effect_id: str, ticks: int):
    bs.combatants[cid].effects[effect_id] = int(ticks)


def _has_effect(bs, cid: CombatantID, effect_id: str) -> bool:
    return bs.combatants[cid].effects.get(effect_id, 0) > 0


def _expire_all_effects_by_end_turn(eng: BattleEngine, bs, n: int = 1):
    """
    end_turn()은 모든 참가자의 effects를 1씩 감소시키고 0이면 삭제한다.
    """
    for _ in range(n):
        eng.end_turn(bs)


def _events(outcome) -> list[str]:
    ev = getattr(outcome, "events", None)
    if ev is None:
        raise AttributeError("apply_skill return object has no '.events'")
    return ev


def _is_blocked(outcome) -> bool:
    return any(e.startswith("SKILL_BLOCKED_BY_EFFECT:") for e in _events(outcome))


# -------------------------
# helpers: indices snapshot
# -------------------------

@dataclass(frozen=True)
class IndicesSnap:
    hit: int
    evade: int
    weak: int
    strong: int
    critical: int

def _snap(bs, A: CombatantID, E: CombatantID, *, crit_stat: str) -> IndicesSnap:
    ix = compute_attack_indices(bs, A, E, crit_stat=crit_stat)
    return IndicesSnap(
        hit=ix.hit_eva.hit,
        evade=ix.hit_eva.evade,
        weak=ix.crit.weak,
        strong=ix.crit.strong,
        critical=ix.crit.critical,
    )


# -------------------------
# Phase 26: index-transform effects + expiry
# -------------------------

@pytest.mark.parametrize(
    "effect_id, delta_hit",
    [
        ("Confusion", -20),
        ("Fear", -10),
        ("Blind", -40),
        ("Slow", -10),
    ],
)
def test_phase26_attacker_hit_effects_apply_and_expire(effect_id: str, delta_hit: int):
    """
    [Phase 26] 공격자(attacker)에게 걸린 상태이상이 HIT 지수에 반영되고,
              tick 감소로 상태이상이 삭제되면 더 이상 반영되지 않아야 한다.

    검증 대상(표 기준):
    - Confusion: HIT -20
    - Fear: HIT -10
    - Blind: HIT -40
    - Slow: HIT -10

    시나리오:
    - baseline indices 측정
    - attacker에 effect를 ticks=1로 부여
    - hit가 baseline + delta_hit 인지 확인
    - end_turn 1회 => effect 삭제
    - hit가 baseline으로 복귀하는지 확인
    """
    eng, bs, A, E = _mk_engine_1v1()

    base = _snap(bs, A, E, crit_stat="STR")
    _set_effect(bs, A, effect_id, ticks=1)

    after = _snap(bs, A, E, crit_stat="STR")
    assert after.hit == base.hit + delta_hit

    _expire_all_effects_by_end_turn(eng, bs, n=1)
    assert not _has_effect(bs, A, effect_id)

    restored = _snap(bs, A, E, crit_stat="STR")
    assert restored.hit == base.hit


@pytest.mark.parametrize(
    "effect_id, delta_evade",
    [
        ("Confusion", -5),
        ("Fear", -50),
        ("Slow", -10),
        ("Bind", -50),
    ],
)
def test_phase26_defender_evade_effects_apply_and_expire(effect_id: str, delta_evade: int):
    """
    [Phase 26] 방어자(defender)에게 걸린 상태이상이 EVADE 지수에 반영되고,
              tick 감소로 상태이상이 삭제되면 더 이상 반영되지 않아야 한다.

    검증 대상(표 기준):
    - Confusion: EVADE -5
    - Fear: EVADE -50
    - Slow: EVADE -10
    - Bind: EVADE -50

    시나리오:
    - baseline indices 측정
    - defender에 effect ticks=1 부여
    - evade가 baseline + delta_evade 인지 확인
    - end_turn 1회 => effect 삭제
    - evade가 baseline으로 복귀하는지 확인
    """
    eng, bs, A, E = _mk_engine_1v1()

    base = _snap(bs, A, E, crit_stat="STR")
    _set_effect(bs, E, effect_id, ticks=1)

    after = _snap(bs, A, E, crit_stat="STR")
    assert after.evade == max(0, base.evade + delta_evade)

    _expire_all_effects_by_end_turn(eng, bs, n=1)
    assert not _has_effect(bs, E, effect_id)

    restored = _snap(bs, A, E, crit_stat="STR")
    assert restored.evade == base.evade


@pytest.mark.parametrize(
    "effect_id, delta_weak, crit_stat",
    [
        # always applies
        ("Burned", -10, "STR"),
        ("Frostbite", -10, "STR"),
        ("Frozen", -15, "STR"),
        ("Burned", -10, "INT"),
        ("Frostbite", -10, "INT"),
        ("Frozen", -15, "INT"),

        # conditional by crit_stat
        ("Stun", -5, "STR"),        # physical only
        ("Stun", 0, "INT"),
        ("Paralysis", 0, "STR"),
        ("Paralysis", -5, "INT"),   # magic only
        ("Corruption", 0, "STR"),
        ("Corruption", -15, "INT"), # magic only
    ],
)
def test_phase26_defender_weak_weight_effects_apply_and_expire(effect_id: str, delta_weak: int, crit_stat: str):
    """
    [Phase 26] 방어자(defender)에 걸린 상태이상이 "약공 피격 지수"에 반영되는지 검증한다.
              구현상 defender의 crit.weak 가중치에 delta가 합산되는 것으로 테스트한다.

    검증 대상(표 기준):
    - Burned: 약공 피격 -10 (항상)
    - Frostbite: 약공 피격 -10 (항상)
    - Frozen: 약공 피격 -15 (항상)
    - Stun: 물리 약공 피격 -5 (crit_stat STR/AGI일 때만)
    - Paralysis: 마법 약공 피격 -5 (crit_stat INT/WIS일 때만)
    - Corruption: 마법 약공 피격 -15 (crit_stat INT/WIS일 때만)

    시나리오:
    - baseline crit.weak 측정
    - defender에 effect ticks=1 부여
    - crit_stat 조건에 따라 weak가 baseline + delta_weak 인지 확인
    - end_turn 1회 => effect 삭제
    - weak가 baseline으로 복귀하는지 확인
    """
    eng, bs, A, E = _mk_engine_1v1()

    base = _snap(bs, A, E, crit_stat=crit_stat)
    _set_effect(bs, E, effect_id, ticks=1)

    after = _snap(bs, A, E, crit_stat=crit_stat)
    assert after.weak == base.weak + delta_weak

    _expire_all_effects_by_end_turn(eng, bs, n=1)
    assert not _has_effect(bs, E, effect_id)

    restored = _snap(bs, A, E, crit_stat=crit_stat)
    assert restored.weak == base.weak


def test_phase26_weakness_increases_attacker_weak_on_physical_only_and_expires():
    """
    [Phase 26] Weakness:
      - 자신의 물리 공격에 대한 약공 지수 +20
      - 즉, attacker가 Weakness일 때 crit_stat이 STR/AGI인 공격에서만
        attacker의 crit.weak 가중치가 +20 되어야 한다.

    시나리오:
    - baseline(STR)과 baseline(INT)의 attacker weak를 각각 측정
    - attacker에 Weakness ticks=1 부여
    - STR에서는 weak +20, INT에서는 변화 없음
    - end_turn 1회로 삭제되면 두 경우 모두 baseline으로 복귀
    """
    eng, bs, A, E = _mk_engine_1v1()

    base_str = _snap(bs, A, E, crit_stat="STR")
    base_int = _snap(bs, A, E, crit_stat="INT")

    _set_effect(bs, A, "Weakness", ticks=1)

    after_str = _snap(bs, A, E, crit_stat="STR")
    after_int = _snap(bs, A, E, crit_stat="INT")

    assert after_str.weak == base_str.weak + 20
    assert after_int.weak == base_int.weak

    _expire_all_effects_by_end_turn(eng, bs, n=1)
    assert not _has_effect(bs, A, "Weakness")

    restored_str = _snap(bs, A, E, crit_stat="STR")
    restored_int = _snap(bs, A, E, crit_stat="INT")
    assert restored_str.weak == base_str.weak
    assert restored_int.weak == base_int.weak


# -------------------------
# Phase 26: skill restriction effects
# -------------------------

def _simple_damage_skill(actor: CombatantID, target: CombatantID, *, action_type: str, crit_stat: str = "STR") -> Skill:
    """
    정상적으로 실행 가능해야 하는 '간단 스킬' (이동/상태부여 없음).
    """
    return Skill(
        skill_id=f"P26_SIMPLE_{action_type}",
        name="simple",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat=crit_stat,
        steps=[
            Step(kind="APPLY_HP_DELTA", target=target, hp_delta=-1, range="ANY", area="SINGLE"),
        ],
    )


def _move_skill(actor: CombatantID, *, action_type: str, crit_stat: str = "STR") -> Skill:
    """
    Bind 테스트용: MOVE step 포함.
    - 실제 MOVE step 필드가 더 필요하더라도,
      Phase 26의 가드는 'steps 검사' 단계에서 막으므로 실행/검증에는 충분하다.
    """
    return Skill(
        skill_id=f"P26_MOVE_{action_type}",
        name="move",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat=crit_stat,
        steps=[
            Step(kind="MOVE_ENGAGE", target=None, range="ANY", area="SINGLE"),
        ],
    )


def _magic_apply_effect_skill(actor: CombatantID, target: CombatantID, *, action_type: str) -> Skill:
    """
    Curse 테스트용: '마법 공격' + 'APPLY_EFFECT 포함' 스킬.
    - 가드에서 막히므로 APPLY_EFFECT 필수 필드가 일부 없어도 통과할 수 있지만,
      안정적으로 필요한 필드를 넣는다.
    """
    return Skill(
        skill_id=f"P26_MAGIC_APPLY_{action_type}",
        name="magic+apply",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="INT",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=target,
                effect_id="Blind",
                effect_duration=1,
                status_inflict=10,
                range="ANY",
                area="SINGLE",
            ),
        ],
    )


def _physical_apply_effect_skill(actor: CombatantID, target: CombatantID, *, action_type: str) -> Skill:
    """
    Curse 테스트용: '물리 공격' + 'APPLY_EFFECT 포함' 스킬(막히면 안 됨).
    """
    return Skill(
        skill_id=f"P26_PHYS_APPLY_{action_type}",
        name="phys+apply",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=target,
                effect_id="Blind",
                effect_duration=1,
                status_inflict=10,
                range="ANY",
                area="SINGLE",
            ),
        ],
    )


def _two_step_skill(actor: CombatantID, target: CombatantID, *, action_type: str, crit_stat: str = "STR") -> Skill:
    """
    Oblivion 테스트용: 2-step 이상 스킬.
    """
    return Skill(
        skill_id=f"P26_2STEP_{action_type}",
        name="two-step",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat=crit_stat,
        steps=[
            Step(kind="APPLY_HP_DELTA", target=target, hp_delta=-1, range="ANY", area="SINGLE"),
            Step(kind="APPLY_HP_DELTA", target=target, hp_delta=-1, range="ANY", area="SINGLE"),
        ],
    )


@pytest.mark.parametrize("effect_id", ["Stun", "Paralysis", "Frozen"])
def test_phase26_action_blocking_effects_block_any_skill(effect_id: str):
    """
    [Phase 26] 행동 불가 상태이상은 어떤 스킬이든 사용을 막아야 한다.
    대상:
    - Stun / Paralysis / Frozen

    시나리오:
    - actor(A)에 effect ticks=2 부여
    - '간단 데미지 스킬' 시전 시도
    기대:
    - SKILL_BLOCKED_BY_EFFECT 이벤트가 발생
    - target HP 변화 없음 (실제 step 적용이 일어나지 않음)
    """
    eng, bs, A, E = _mk_engine_1v1()

    _set_effect(bs, A, effect_id, ticks=2)

    before = bs.combatants[E].hp
    skill = _simple_damage_skill(A, E, action_type="MAIN", crit_stat="STR")

    out = eng.apply_skill(bs, skill)
    assert _is_blocked(out)
    assert bs.combatants[E].hp == before


def test_phase26_bind_blocks_move_skills_but_allows_non_move():
    """
    [Phase 26] Bind:
      - 이동이 포함된 스킬 사용 불가
      - 그리고 EVADE -50(지수 보정형)은 별도 테스트에서 검증됨

    시나리오:
    - actor(A)에 Bind ticks=2 부여
    - non-move 스킬은 막히지 않아야 함
    - move 스킬(MOVE step 포함)은 막혀야 함
    """
    # non-move allowed
    eng1, bs1, A1, E1 = _mk_engine_1v1()
    _set_effect(bs1, A1, "Bind", ticks=2)
    out1 = eng1.apply_skill(bs1, _simple_damage_skill(A1, E1, action_type="MAIN"))
    assert not _is_blocked(out1)

    # move blocked
    eng2, bs2, A2, _ = _mk_engine_1v1()
    _set_effect(bs2, A2, "Bind", ticks=2)
    out2 = eng2.apply_skill(bs2, _move_skill(A2, action_type="MAIN"))
    assert _is_blocked(out2)


def test_phase26_oblivion_blocks_multi_step_only():
    """
    [Phase 26] Oblivion:
      - 2스텝 이상 스킬 사용 불가

    시나리오:
    - actor(A)에 Oblivion ticks=2
    - 1-step 스킬은 허용
    - 2-step 스킬은 차단
    """
    # 1-step allowed
    eng1, bs1, A1, E1 = _mk_engine_1v1()
    _set_effect(bs1, A1, "Oblivion", ticks=2)
    out1 = eng1.apply_skill(bs1, _simple_damage_skill(A1, E1, action_type="MAIN"))
    assert not _is_blocked(out1)

    # 2-step blocked
    eng2, bs2, A2, E2 = _mk_engine_1v1()
    _set_effect(bs2, A2, "Oblivion", ticks=2)
    out2 = eng2.apply_skill(bs2, _two_step_skill(A2, E2, action_type="MAIN"))
    assert _is_blocked(out2)


def test_phase26_curse_blocks_magic_attack_or_apply_effect_but_not_others():
    """
    [Phase 26] Curse:
      - "마법 공격" OR "상태이상 부여 포함" 스킬 사용 불가

    시나리오:
    - actor(A)에 Curse ticks=2
    - (막힘) magic + apply_effect
    - (막힘) magic + no apply_effect
    - (막힘) physical + apply_effect
    - (허용) physical + no apply_effect
    """
    # (blocked) magic + apply_effect
    eng1, bs1, A1, E1 = _mk_engine_1v1()
    _set_effect(bs1, A1, "Curse", ticks=2)
    out1 = eng1.apply_skill(bs1, _magic_apply_effect_skill(A1, E1, action_type="MAIN"))
    assert _is_blocked(out1)

    # (blocked) magic + no apply_effect
    eng2, bs2, A2, E2 = _mk_engine_1v1()
    _set_effect(bs2, A2, "Curse", ticks=2)
    out2 = eng2.apply_skill(bs2, _simple_damage_skill(A2, E2, action_type="MAIN", crit_stat="INT"))
    assert _is_blocked(out2)

    # (blocked) physical + apply_effect
    eng3, bs3, A3, E3 = _mk_engine_1v1()
    _set_effect(bs3, A3, "Curse", ticks=2)
    out3 = eng3.apply_skill(bs3, _physical_apply_effect_skill(A3, E3, action_type="MAIN"))
    assert _is_blocked(out3)

    # (allowed) physical + no apply_effect
    eng4, bs4, A4, E4 = _mk_engine_1v1()
    _set_effect(bs4, A4, "Curse", ticks=2)
    out4 = eng4.apply_skill(bs4, _simple_damage_skill(A4, E4, action_type="MAIN", crit_stat="STR"))
    assert not _is_blocked(out4)
