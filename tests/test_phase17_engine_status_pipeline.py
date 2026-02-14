import random

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.types import CombatantID
from battle_system.core.commands import Step, Skill
from battle_system.rules.indices.status import compute_status_resist_index
from battle_system.timebase.durations import turns_to_ticks_for_battle


def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int = 50) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def _find_seed_for_roll_success(*, inflict: int, resist: int, want_success: bool, limit: int = 2000) -> int:
    """
    roll_status_success의 규칙(이 프로젝트에서 이미 구현된 형태)을 가정하고 seed를 탐색한다.
    - roll in [1, inflict+resist]
    - success <=> roll <= inflict
    """
    for seed in range(limit):
        random.seed(seed)
        roll = random.randint(1, inflict + resist)
        success = (roll <= inflict)
        if success == want_success:
            return seed
    raise RuntimeError("No seed found within limit; increase limit or adjust indices.")


def test_phase17_apply_effect_engine_computes_resist_and_rolls():
    """
    TITLE: APPLY_EFFECT에서 엔진이 저항 지수를 직접 계산해 판정을 수행하는지 검증
    PURPOSE:
      - Step에 status_resist가 더 이상 없을 때, 엔진은 대상 스탯과 effect_id(status_id)로부터
        compute_status_resist_index를 호출해 resist를 계산해야 한다.
      - 성공 시 대상 CombatantState.effects에 effect가 추가되어야 한다.
    SETUP:
      - A1(시전자) vs E1(대상)
      - effect_id="BLEEDING" (보조 스탯 STR)
      - E1 stats: CON=10, STR=8  => resist = CON + (STR*0.5) = 10 + 4 = 14
      - inflict(부여 지수)는 임의로 30을 사용
      - seed 탐색으로 success=True가 보장되는 seed를 찾아 실행한다.
    EXPECTED:
      - STATUS_CHECK 로그에 resist 값이 포함
      - EFFECT_APPLIED 로그가 존재
      - bs.combatants["E1"].effects["BLEEDING"] == duration(ticks)
    """
    eng = BattleEngine()

    a1 = _mk_char("A1", level=5, stats=Stats(str=5, agi=5, con=5, int=5, wis=5, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=8, agi=1, con=10, int=1, wis=1, cha=0))

    bs = eng.create_battle([a1], [e1])

    # 기대 resist 계산
    resist = compute_status_resist_index(stats=e1.stats, status_id="BLEEDING")
    assert resist.resistible is True
    assert resist.value == 14

    inflict = 30
    duration_turns = 3
    expected_ticks = turns_to_ticks_for_battle(bs, duration_turns)
    seed_success = _find_seed_for_roll_success(inflict=inflict, resist=resist.value, want_success=True)

    random.seed(seed_success)
    skill = Skill(
        skill_id="test_apply_eff",
        name="test_apply_eff",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=CombatantID("E1"),
                effect_id="BLEEDING",
                effect_duration=duration_turns,
                status_inflict=inflict,
            )
        ],
    )
    out = eng.apply_skill(bs, skill)

    print("\n[Phase17 APPLY_EFFECT]")
    print(f"computed_resist={resist.value}, inflict={inflict}, seed_success={seed_success}")
    for e in out.events:
        print(" ", e)

    # 로그에 resist가 계산되어 찍히는지
    assert any("STATUS_CHECK:" in e and "effect=BLEEDING" in e and f"resist={resist.value}" in e for e in out.events)
    assert any("success=True" in e for e in out.events)
    assert any("EFFECT_APPLIED:" in e and "+BLEEDING" in e for e in out.events)

    assert bs.combatants[CombatantID("E1")].effects["BLEEDING"] == expected_ticks


def test_phase17_remove_effect_uses_fixed_dispel_inflict_20_and_ignores_step_value():
    """
    TITLE: REMOVE_EFFECT 해제 판정에서 inflict가 항상 20으로 고정되는지 검증
    PURPOSE:
      - 사용자가 결정한 규칙: 상태이상 해제 시 사용하는 상태이상 지수(inflict)는 상태 종류와 무관하게 20 고정.
      - 해제는 부여 판정의 반대로 해석:
          success=True  => 해제 실패(유지)
          success=False => 해제 성공(삭제)
      - seed 탐색으로 해제 성공/실패를 모두 재현한다.
    EXPECTED:
      - 두 실행 모두 DISPEL_CHECK 로그에 inflict=20 포함
      - 해제 실패 시 effects에 남아있음
      - 해제 성공 시 effects에서 제거됨
    """
    eng = BattleEngine()

    a1 = _mk_char("A1", level=5, stats=Stats(str=5, agi=5, con=5, int=5, wis=5, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=8, agi=1, con=10, int=1, wis=1, cha=0))

    bs = eng.create_battle([a1], [e1])
    tgt = CombatantID("E1")

    # 상태 선부여(전제)
    bs.combatants[tgt].effects["BLEEDING"] = 3

    resist = compute_status_resist_index(stats=e1.stats, status_id="BLEEDING")
    assert resist.resistible is True
    assert resist.value == 14

    DISPEL_INFLICT = 20

    seed_dispeL_fail = _find_seed_for_roll_success(inflict=DISPEL_INFLICT, resist=resist.value, want_success=True)
    seed_dispeL_succ = _find_seed_for_roll_success(inflict=DISPEL_INFLICT, resist=resist.value, want_success=False)

    print("\n[Phase17 REMOVE_EFFECT] computed_resist=", resist.value)
    print(" seed_fail(success=True -> dispel fail) =", seed_dispeL_fail)
    print(" seed_succ(success=False -> dispel success) =", seed_dispeL_succ)

    # 1) 해제 실패(success True => 유지)
    random.seed(seed_dispeL_fail)
    skill1 = Skill(
        skill_id="test_remove_1",
        name="test_remove_1",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="REMOVE_EFFECT",
                target=tgt,
                effect_id="BLEEDING",
            )
        ],
    )
    out1 = eng.apply_skill(bs, skill1)
    for e in out1.events:
        print(" ", e)

    assert "BLEEDING" in bs.combatants[tgt].effects  # 유지
    assert any("DISPEL_CHECK:" in e and "effect=BLEEDING" in e and "inflict=20" in e for e in out1.events)
    assert any("DISPEL_FAILED:" in e for e in out1.events)

    # 2) 해제 성공(success False => 삭제)
    random.seed(seed_dispeL_succ)
    skill2 = Skill(
        skill_id="test_remove_2",
        name="test_remove_2",
        actor=bs.current_actor_id(),
        action_type="SUB",
        steps=[
            Step(
                kind="REMOVE_EFFECT",
                target=tgt,
                effect_id="BLEEDING",
            )
        ],
    )
    out2 = eng.apply_skill(bs, skill2)
    for e in out2.events:
        print(" ", e)

    assert "BLEEDING" not in bs.combatants[tgt].effects  # 삭제
    assert any("DISPEL_CHECK:" in e and "effect=BLEEDING" in e and "inflict=20" in e for e in out2.events)
    assert any("DISPEL_SUCCESS:" in e for e in out2.events)
