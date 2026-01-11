import random

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.types import CombatantID
from battle_system.core.commands import Step
from battle_system.rules.indices.status import compute_status_resist_index


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
      - 그 resist를 roll_status_success에 넣어 roll/success가 로그로 남아야 한다.
      - 성공 시 대상 CombatantState.effects에 effect가 추가되어야 한다.
    SETUP:
      - A1(시전자) vs E1(대상)
      - effect_id="BLEEDING" (보조 스탯 STR)
      - E1 stats: CON=10, STR=8  => resist = CON + (STR*0.5) = 10 + 4 = 14
      - inflict(부여 지수)는 임의로 30을 사용(스킬에서 온 값이라고 가정)
      - seed 탐색으로 success=True가 보장되는 seed를 찾아 실행한다.
    STEPS:
      1) battle 생성 후, APPLY_EFFECT step 1개를 apply_steps로 실행한다.
      2) 로그에 resist=14가 찍히는지 확인한다.
      3) 성공 시 E1.effects에 "BLEEDING"이 duration으로 들어가는지 확인한다.
    EXPECTED:
      - STATUS_CHECK 로그에 resist=14, roll=..., success=True가 포함
      - EFFECT_APPLIED 로그가 존재
      - bs.combatants["E1"].effects["BLEEDING"] == duration
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
    seed_success = _find_seed_for_roll_success(inflict=inflict, resist=resist.value, want_success=True)

    random.seed(seed_success)
    out = eng.apply_steps(
        bs,
        [
            Step(
                kind="APPLY_EFFECT",
                actor=bs.current_actor_id(),
                target=CombatantID("E1"),
                effect_id="BLEEDING",
                effect_duration=3,
                status_inflict=inflict,
                action_type="MAIN",
            )
        ],
    )

    print("\n[Phase17 APPLY_EFFECT]")
    print(f"computed_resist={resist.value}, inflict={inflict}, seed_success={seed_success}")
    for e in out.events:
        print(" ", e)

    # 로그에 resist가 계산되어 찍히는지
    assert any("STATUS_CHECK:" in e and "effect=BLEEDING" in e and f"resist={resist.value}" in e for e in out.events)
    assert any("success=True" in e for e in out.events)
    assert any("EFFECT_APPLIED:" in e and "+BLEEDING(3)" in e for e in out.events)

    assert bs.combatants[CombatantID("E1")].effects["BLEEDING"] == 3


def test_phase17_remove_effect_uses_fixed_dispel_inflict_20_and_ignores_step_value():
    """
    TITLE: REMOVE_EFFECT 해제 판정에서 inflict가 항상 20으로 고정되는지 검증
    PURPOSE:
      - 사용자가 결정한 규칙: 상태이상 해제 시 사용하는 상태이상 지수(inflict)는 상태 종류와 무관하게 20 고정.
      - 따라서 Step에 status_inflict가 어떤 값으로 들어오든(혹은 들어오지 않든),
        엔진의 DISPEL_CHECK 로그에는 inflict=20이 찍혀야 한다.
      - 또한 해제는 부여 판정의 반대로 해석:
          success=True  => 해제 실패(유지)
          success=False => 해제 성공(삭제)
      - seed 탐색으로 해제 성공/실패를 모두 재현한다.
    SETUP:
      - 먼저 E1에 BLEEDING 효과를 강제로 부여해둔다(상태 존재 전제).
      - E1 stats: CON=10, STR=8 => resist=14 (BLEEDING)
      - dispel inflict=20 고정
      - seed 탐색:
         - success=True가 나오는 seed: 해제 실패(유지)
         - success=False가 나오는 seed: 해제 성공(삭제)
      - Step.status_inflict에는 일부러 999를 넣어도 무시되어야 한다.
    STEPS:
      1) seed_fail(=success True)로 REMOVE_EFFECT 실행 -> 효과 유지 확인
      2) seed_succ(=success False)로 REMOVE_EFFECT 실행 -> 효과 삭제 확인
      3) 두 케이스 모두 로그에 inflict=20이 찍히는지 확인
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
    out1 = eng.apply_steps(
        bs,
        [
            Step(
                kind="REMOVE_EFFECT",
                actor=bs.current_actor_id(),
                target=tgt,
                effect_id="BLEEDING",
                # 일부러 이상한 값 넣어도 무시되어야 함
                status_inflict=999,
                action_type="MAIN",
            )
        ],
    )
    for e in out1.events:
        print(" ", e)

    assert "BLEEDING" in bs.combatants[tgt].effects  # 유지
    assert any("DISPEL_CHECK:" in e and "effect=BLEEDING" in e and "inflict=20" in e for e in out1.events)
    assert any("DISPEL_FAILED:" in e for e in out1.events)

    # 2) 해제 성공(success False => 삭제)
    random.seed(seed_dispeL_succ)
    out2 = eng.apply_steps(
        bs,
        [
            Step(
                kind="REMOVE_EFFECT",
                actor=bs.current_actor_id(),
                target=tgt,
                effect_id="BLEEDING",
                # 여기도 무시되어야 함
                status_inflict=999,
                action_type="SUB",
            )
        ],
    )
    for e in out2.events:
        print(" ", e)

    assert "BLEEDING" not in bs.combatants[tgt].effects  # 삭제
    assert any("DISPEL_CHECK:" in e and "effect=BLEEDING" in e and "inflict=20" in e for e in out2.events)
    assert any("DISPEL_SUCCESS:" in e for e in out2.events)
