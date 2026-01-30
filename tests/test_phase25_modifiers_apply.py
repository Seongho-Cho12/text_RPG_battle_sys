from __future__ import annotations

import re
from typing import Tuple

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef, ModifierInstance
from battle_system.core.commands import Skill, Step
from battle_system.rules.indices.facade import compute_attack_indices


# -------------------------
# helpers
# -------------------------

def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int = 50) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def _mk_engine_1v1() -> Tuple[BattleEngine, object, CombatantID, CombatantID]:
    """
    1v1 전투 생성(테스트용).
    - A가 선턴이 되도록 AGI 크게.
    """
    eng = BattleEngine()
    a = _mk_char("A", level=10, stats=Stats(str=10, agi=30, con=10, int=10, wis=10, cha=10), max_hp=50)
    e = _mk_char("E", level=10, stats=Stats(str=10, agi=5,  con=10, int=10, wis=10, cha=10), max_hp=50)
    bs = eng.create_battle([a], [e])
    assert bs.current_actor_id() == CombatantID("A")
    return eng, bs, CombatantID("A"), CombatantID("E")


def _add_mod(bs, cid: CombatantID, *, key: str, delta: int, ticks: int = 9999):
    """
    테스트에서는 apply_modifier step을 거치지 않고 직접 넣는다.
    목적: 'modifier가 계산에 반영되는가'만 검증.
    """
    bs.combatants[cid].modifiers.append(
        ModifierInstance(mid="T", key=key, delta=int(delta), ticks_left=int(ticks))
    )


def _events(outcome) -> list[str]:
    ev = getattr(outcome, "events", None)
    if ev is None:
        raise AttributeError("apply_skill return object has no '.events'")
    return ev


def _find_status_check(events: list[str]) -> str:
    """
    STATUS_CHECK 로그 한 줄을 찾는다.
    """
    for e in events:
        if e.startswith("STATUS_CHECK:"):
            return e
    raise AssertionError("STATUS_CHECK log not found")


def _parse_inflict_resist(line: str) -> tuple[int, int]:
    """
    STATUS_CHECK: ... inflict=XX resist=YY ... 에서 XX/YY 뽑기
    """
    m = re.search(r"inflict=(\d+)\s+resist=([0-9]+)", line)
    assert m, f"Cannot parse inflict/resist from: {line}"
    return int(m.group(1)), int(m.group(2))


# -------------------------
# Phase 25 tests
# -------------------------

def test_phase25_attack_indices_apply_hit_evade_and_crit_weight_modifiers():
    """
    [Phase 25] 지속 modifier(HIT/EVADE/WEAK/STRONG/CRITICAL)가
              compute_attack_indices 결과에 그대로 가산 반영되어야 한다.

    시나리오:
    - 1v1 전투에서, baseline indices를 먼저 계산한다.
    - attacker(A)에 HIT/WEAK/STRONG/CRITICAL modifier를 붙인다.
    - defender(E)에 EVADE modifier를 붙인다.
    기대:
    - hit는 +HIT, evade는 +EVADE
    - crit.weak/strong/critical은 각 modifier delta만큼 증가
    """
    eng, bs, A, E = _mk_engine_1v1()

    base = compute_attack_indices(bs, A, E, crit_stat="STR")
    base_hit = base.hit_eva.hit
    base_evade = base.hit_eva.evade
    base_w = base.crit.weak
    base_s = base.crit.strong
    base_c = base.crit.critical

    _add_mod(bs, A, key="HIT", delta=7)
    _add_mod(bs, E, key="EVADE", delta=5)
    _add_mod(bs, A, key="WEAK", delta=11)
    _add_mod(bs, A, key="STRONG", delta=13)
    _add_mod(bs, A, key="CRITICAL", delta=17)

    after = compute_attack_indices(bs, A, E, crit_stat="STR")

    assert after.hit_eva.hit == base_hit + 7
    assert after.hit_eva.evade == base_evade + 5
    assert after.crit.weak == base_w + 11
    assert after.crit.strong == base_s + 13
    assert after.crit.critical == base_c + 17


def test_phase25_defense_modifier_increases_weak_weight_physical_and_magic_split():
    """
    [Phase 25] PHYSICAL_DEFENSE / MAGIC_DEFENSE는
              피격 시 WEAK 가중치를 +delta 만큼 올려야 한다.

    규칙:
    - crit_stat이 STR/AGI면 물리 -> PHYSICAL_DEFENSE만 적용
    - crit_stat이 INT/WIS면 마법 -> MAGIC_DEFENSE만 적용

    시나리오:
    - defender(E)에 PHYSICAL_DEFENSE=+9, MAGIC_DEFENSE=+21을 동시에 부여.
    - crit_stat=STR(물리)로 indices 계산: weak는 +9만 반영되어야 한다.
    - crit_stat=INT(마법)로 indices 계산: weak는 +21만 반영되어야 한다.
    기대:
    - 물리/마법에 따라 weak 증가량이 정확히 달라진다.
    """
    eng, bs, A, E = _mk_engine_1v1()

    base_str = compute_attack_indices(bs, A, E, crit_stat="STR").crit.weak
    base_int = compute_attack_indices(bs, A, E, crit_stat="INT").crit.weak

    _add_mod(bs, E, key="PHYSICAL_DEFENSE", delta=9)
    _add_mod(bs, E, key="MAGIC_DEFENSE", delta=21)

    after_str = compute_attack_indices(bs, A, E, crit_stat="STR").crit.weak
    after_int = compute_attack_indices(bs, A, E, crit_stat="INT").crit.weak

    assert after_str == base_str + 9
    assert after_int == base_int + 21


def test_phase25_status_inflict_resist_modifiers_adjust_roll_inputs_in_logs():
    """
    [Phase 25] STATUS_INFLICT / STATUS_RESIST는
              상태이상 판정(roll_status_success) 입력값 inflict/resist에 가산되어야 한다.

    테스트 방식(안정성 목적):
    - 성공/실패 결과는 RNG 영향이 있으므로 직접 검증하지 않는다.
    - 대신 STATUS_CHECK 로그에 기록된 inflict/resist 값이
      modifier 적용 전/후로 정확히 변하는지 비교한다.

    시나리오:
    - 동일한 skill(APPLY_EFFECT)을 modifier 없이 1회 실행해 baseline inflict/resist를 로그에서 추출.
    - attacker에 STATUS_INFLICT +40, defender에 STATUS_RESIST +15를 부여한 뒤 같은 skill 실행.
    기대:
    - 두 번째 실행의 inflict는 baseline +40
    - 두 번째 실행의 resist는 baseline +15
    """
    eng, bs, A, E = _mk_engine_1v1()

    # 주의: effect_id는 레포에 존재하는 StatusID 여야 한다.
    # 보통은 "STUN" 같은 대문자 ID를 사용 (status.py Literal 기준).
    skill = Skill(
        skill_id="P25_STATUS_MODS",
        name="status mods",
        actor=A,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=E,
                effect_id="STUN",
                effect_duration=1,
                status_inflict=30,
                range="ANY",
                area="SINGLE",
            ),
        ],
    )

    skill_2 = Skill(
        skill_id="P25_STATUS_MODS",
        name="status mods",
        actor=A,
        action_type="SUB",
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=E,
                effect_id="STUN",
                effect_duration=1,
                status_inflict=30,
                range="ANY",
                area="SINGLE",
            ),
        ],
    )

    out1 = eng.apply_skill(bs, skill)
    line1 = _find_status_check(_events(out1))
    inf1, res1 = _parse_inflict_resist(line1)

    _add_mod(bs, A, key="STATUS_INFLICT", delta=40)
    _add_mod(bs, E, key="STATUS_RESIST", delta=15)

    out2 = eng.apply_skill(bs, skill_2)
    line2 = _find_status_check(_events(out2))
    inf2, res2 = _parse_inflict_resist(line2)

    assert inf2 == inf1 + 40
    assert res2 == res1 + 15
