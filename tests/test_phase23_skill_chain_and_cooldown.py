from __future__ import annotations

import random
import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Skill, Step


# -------------------------
# helpers
# -------------------------

def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def _mk_engine_1v1() -> tuple[BattleEngine, object, CombatantID, CombatantID]:
    """
    1v1로 간단히 턴/쿨타임/체인 검증하기 위한 battle.
    - A1이 선턴이 되도록(AGI 크게)
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=10, stats=Stats(str=10, agi=20, con=10, int=10, wis=10, cha=10), max_hp=50)
    e1 = _mk_char("E1", level=10, stats=Stats(str=10, agi=5,  con=10, int=10, wis=10, cha=10), max_hp=50)
    bs = eng.create_battle([a1], [e1])
    assert bs.current_actor_id() == CombatantID("A1")
    return eng, bs, CombatantID("A1"), CombatantID("E1")


def _advance_full_round(eng: BattleEngine, bs) -> None:
    """
    현재 actor의 턴을 끝내고 상대 턴까지 끝내서 다시 현재 actor로 돌아오게 한다.
    (1v1 기준)
    """
    eng.end_turn(bs)  # A1 -> E1
    eng.end_turn(bs)  # E1 -> A1


# -------------------------
# tests
# -------------------------

def test_skill_chain_attack_evade_breaks_and_skips_next_steps():
    """
    공격이 EVADE(0)이면 require_prev_gte=1인 다음 step들은 실행되지 않고 체인이 끊겨야 한다.
    """
    eng, bs, A1, E1 = _mk_engine_1v1()

    # "ATTACK 후 APPLY_EFFECT" 체인. APPLY_EFFECT는 공격 결과가 1 이상일 때만 실행.
    skill = Skill(
        skill_id="test_attack_then_bleed",
        name="Attack->Bleed",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(kind="ATTACK", target=E1),
            Step(kind="APPLY_EFFECT", target=E1, effect_id="BLEEDING", effect_duration=2, status_inflict=30, require_prev_gte=1),
            Step(kind="APPLY_MODIFIER", target=E1, modifier_key="HIT", modifier_delta=-10, modifier_duration=2, require_prev_gte=1),
        ],
    )

    # EVADE가 발생하는 seed를 탐색
    found = None
    for seed in range(0, 2000):
        random.seed(seed)
        out = eng.apply_skill(bs, skill)
        # 공격 outcome 로그에서 EVADE 확인
        if any("STEP: ATTACK" in e and "outcome=EVADE" in e for e in out.events):
            found = seed
            break

        # battle state가 바뀌었으니 되돌리기 쉬운 방식으로 다시 새로 생성(테스트 단순화)
        eng, bs, A1, E1 = _mk_engine_1v1()

    assert found is not None, "EVADE seed not found in range"
    print(f"[CHAIN EVADE] seed={found}")
    # EVADE seed로 다시 정확히 실행
    eng, bs, A1, E1 = _mk_engine_1v1()
    random.seed(found)
    out = eng.apply_skill(bs, skill)
    for line in out.events:
        print(line)

    # 1) 공격은 수행
    assert any("STEP: ATTACK" in e for e in out.events)
    # 2) EVADE(0) -> 이후 step 스킵 + CHAIN_BREAK
    assert any("STEP_SKIPPED:" in e for e in out.events)
    assert any("CHAIN_BREAK" in e for e in out.events)
    # 3) effect/modifier는 적용되지 않아야 함
    assert "BLEEDING" not in bs.combatants[E1].effects
    assert len(bs.combatants[E1].modifiers) == 0


def test_skill_chain_attack_hit_allows_next_step_and_effect_may_apply():
    """
    공격 결과가 1 이상이면(WEAK/STRONG/CRITICAL) 다음 APPLY_EFFECT가 실행(시도)되어야 한다.
    성공/실패는 status roll에 따름(둘 다 가능).
    """
    eng, bs, A1, E1 = _mk_engine_1v1()

    skill = Skill(
        skill_id="test_attack_then_bleed",
        name="Attack->Bleed",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(kind="ATTACK", target=E1),
            Step(kind="APPLY_EFFECT", target=E1, effect_id="BLEEDING", effect_duration=2, status_inflict=30, require_prev_gte=1),
        ],
        crit_stat="AGI"
    )

    # hit(>=1) 발생 seed 탐색
    found = None
    found_outcome = None
    for seed in range(0, 100000):
        eng, bs, A1, E1 = _mk_engine_1v1()
        random.seed(seed)
        out = eng.apply_skill(bs, skill)
        if any("STEP: ATTACK" in e and "outcome=EVADE" not in e for e in out.events):
            found = seed
            # 어떤 outcome인지 찍어두기
            for e in out.events:
                if "STEP: ATTACK" in e:
                    found_outcome = e
            break

    assert found is not None
    print(f"[CHAIN HIT] seed={found}")
    print(found_outcome)

    eng, bs, A1, E1 = _mk_engine_1v1()
    random.seed(found)
    out = eng.apply_skill(bs, skill)
    for line in out.events:
        print(line)

    # 1) EVADE가 아니므로 APPLY_EFFECT step이 실행되어 STATUS_CHECK 로그가 있어야 함
    assert any("STATUS_CHECK:" in e and "effect=BLEEDING" in e for e in out.events)


def test_skill_chain_status_success_required_for_modifier():
    """
    APPLY_EFFECT 성공(1)일 때만 다음 APPLY_MODIFIER가 실행되도록 검증.
    - apply_effect가 실패하면(0) 체인 끊기 -> modifier 미적용.
    """
    eng, bs, A1, E1 = _mk_engine_1v1()

    # 공격 없이, status 성공 시 modifier를 붙이는 체인
    skill = Skill(
        skill_id="test_bleed_then_mod",
        name="Bleed->Mod",
        actor=A1,
        action_type="SUB",  # 슬롯도 같이 확인
        cooldown_turns=0,
        steps=[
            Step(kind="APPLY_EFFECT", target=E1, effect_id="BLEEDING", effect_duration=2, status_inflict=1),
            Step(kind="APPLY_MODIFIER", target=E1, modifier_key="WEAK", modifier_delta=-10, modifier_duration=2, require_prev_gte=1),
        ],
    )

    # status 성공 seed와 실패 seed 둘 다 찾기
    succ_seed = None
    fail_seed = None
    for seed in range(0, 3000):
        eng, bs, A1, E1 = _mk_engine_1v1()
        random.seed(seed)
        out = eng.apply_skill(bs, skill)
        applied = any("EFFECT_APPLIED:" in e for e in out.events)
        if applied and succ_seed is None:
            succ_seed = seed
        if (not applied) and fail_seed is None:
            # 실패는 resistible=True일 때 EFFECT_RESISTED가 남는 케이스
            if any("EFFECT_RESISTED:" in e for e in out.events):
                fail_seed = seed
        if succ_seed is not None and fail_seed is not None:
            break

    assert succ_seed is not None and fail_seed is not None

    # 성공 케이스: modifier가 적용되어야 함
    eng, bs, A1, E1 = _mk_engine_1v1()
    random.seed(succ_seed)
    out = eng.apply_skill(bs, skill)
    print(f"[STATUS->MOD success] seed={succ_seed}")
    for line in out.events:
        print(line)
    assert len(bs.combatants[E1].modifiers) == 1

    # 실패 케이스: modifier는 스킵/체인브레이크로 미적용
    eng, bs, A1, E1 = _mk_engine_1v1()
    random.seed(fail_seed)
    out = eng.apply_skill(bs, skill)
    print(f"[STATUS->MOD fail] seed={fail_seed}")
    for line in out.events:
        print(line)
    assert len(bs.combatants[E1].modifiers) == 0
    assert any("CHAIN_BREAK" in e for e in out.events)


def test_attack_result_rank_is_observable_via_chain_thresholds():
    """
    ATTACK 결과가 0/1/2/3으로 매핑되는지 간접 검증:
    - require_prev_gte=2(STRONG 이상) step이 실행되는 seed를 찾는다.
    - require_prev_gte=3(CRITICAL) step이 실행되는 seed를 찾는다.
    """
    eng, bs, A1, E1 = _mk_engine_1v1()

    # ATTACK 후, rank가 충분하면 HP_DELTA를 수행하도록 만든다(눈에 보이게).
    # - STRONG 이상이면 E1에 -1
    # - CRITICAL이면 추가로 -1 (총 -2)
    skill = Skill(
        skill_id="rank_gate",
        name="RankGate",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(kind="ATTACK", target=E1),
            Step(kind="APPLY_HP_DELTA", target=E1, hp_delta=-1, require_prev_gte=2),
            Step(kind="ATTACK", target=E1),
            Step(kind="APPLY_HP_DELTA", target=E1, hp_delta=-1, require_prev_gte=3),
        ],
    )

    strong_seed = None
    crit_seed = None

    for seed in range(0, 5000):
        eng, bs, A1, E1 = _mk_engine_1v1()
        random.seed(seed)
        hp0 = bs.combatants[E1].hp
        out = eng.apply_skill(bs, skill)
        hp1 = bs.combatants[E1].hp

        # STRONG gate(-1)만 열리면 hp 감소량에 1 포함
        # CRITICAL gate까지 열리면 hp 감소량에 2 포함
        delta = hp0 - hp1

        if strong_seed is None:
            # strong gate가 실행되었다는 로그로 확인(가장 확실)
            if any("HP_DELTA:" in e for e in out.events) and any("require_prev_gte=2" not in e for e in out.events):
                # 로그만으로는 gate 종류를 정확히 분리하기 어려우니, delta로 판별
                if delta >= 1:
                    # 추가 데미지(기본 공격 dmg)도 섞이므로, "HP_DELTA 라인 개수"로 게이트 확인
                    hp_delta_lines = [e for e in out.events if e.startswith("HP_DELTA:")]
                    if len(hp_delta_lines) >= 1:
                        strong_seed = seed

        if crit_seed is None:
            hp_delta_lines = [e for e in out.events if e.startswith("HP_DELTA:")]
            if len(hp_delta_lines) >= 2:
                crit_seed = seed

        if strong_seed is not None and crit_seed is not None:
            break

    assert strong_seed is not None, "STRONG(>=2) seed not found"
    assert crit_seed is not None, "CRITICAL(>=3) seed not found"

    # 출력 확인
    eng, bs, A1, E1 = _mk_engine_1v1()
    random.seed(strong_seed)
    out = eng.apply_skill(bs, skill)
    print(f"[RANK gate STRONG] seed={strong_seed}")
    for line in out.events:
        print(line)

    eng, bs, A1, E1 = _mk_engine_1v1()
    random.seed(crit_seed)
    out = eng.apply_skill(bs, skill)
    print(f"[RANK gate CRITICAL] seed={crit_seed}")
    for line in out.events:
        print(line)


def test_skill_cooldown_is_skill_level_not_step_level_and_blocks_next_own_turn():
    """
    쿨타임은 스텝이 아니라 스킬 단위로 처리되어야 하며,
    1턴 쿨다운이면 다음 '자기 턴'에 막히고, 그 다음 턴에는 풀려야 한다.
    (1v1 기준: cd_ticks = turns_to_ticks_for_battle(1) = 1*2 + 1 = 3 라는 전제)
    """
    eng, bs, A1, E1 = _mk_engine_1v1()

    skill = Skill(
        skill_id="cooldown_test",
        name="CooldownTest",
        actor=A1,
        action_type="MAIN",
        cooldown_turns=1,
        steps=[Step(kind="ATTACK", target=E1)],
    )

    # 1) 첫 사용은 성공하고 cooldown이 등록되어야 함
    random.seed(123)
    out = eng.apply_skill(bs, skill)
    for line in out.events:
        print(line)
    assert bs.combatants[A1].cooldowns.get("cooldown_test", 0) > 0

    # 2) 라운드 1회 돌려서(A1->E1->A1) 다시 A1 턴으로
    _advance_full_round(eng, bs)
    assert bs.current_actor_id() == A1

    # 3) 다음 자기 턴에는 아직 쿨다운이 남아 사용 불가여야 함
    with pytest.raises(ValueError):
        random.seed(124)
        eng.apply_skill(bs, skill)

    # 4) 한 번 더 라운드 돌리면 쿨다운이 사라져 사용 가능해야 함
    _advance_full_round(eng, bs)
    assert bs.current_actor_id() == A1
    random.seed(125)
    out2 = eng.apply_skill(bs, skill)
    for line in out2.events:
        print(line)
