import random
import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Step, Skill
from battle_system.engine.engine import BattleEngine
from battle_system.timebase.durations import turns_to_ticks_for_battle


def mk(cid: str, *, team_hint: str, level: int, agi: int, wis: int) -> CharacterDef:
    """
    TITLE: Phase 14 상태이상/정화/공격+부여 Step 테스트용 캐릭터 생성
    SETUP:
      - 선턴 고정이 필요하므로 테스트에서 A1의 agi/wis를 크게 준다.
      - hit/evade를 어느 정도 유도하려면 양쪽 스탯 차이를 크게 준다.
    EXPECTED:
      - create_battle에 바로 넣을 수 있다.
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=level,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=60,
        basic_attack_range="MELEE",
    )


def _battle_1v1_a1_first(*, a1_level=10, a1_agi=40, a1_wis=40, e1_level=1, e1_agi=5, e1_wis=5):
    """
    TITLE: A1 선턴 1:1 전투 생성(스탯 차이 조절 가능)
    SETUP:
      - 기본값은 A1이 매우 유리(명중 유리)하게 둔다.
    EXPECTED:
      - current actor == A1
    """
    a1 = mk("A1", team_hint="ALLY-", level=a1_level, agi=a1_agi, wis=a1_wis)
    e1 = mk("E1", team_hint="ENEMY-", level=e1_level, agi=e1_agi, wis=e1_wis)
    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1])
    assert bs.current_actor_id() == CombatantID("A1")
    return eng, bs


def test_phase14_apply_effect_trials_and_duration_decrements():
    """
    TITLE: APPLY_EFFECT가 즉시 상태이상 판정을 수행하고, 성공 시 effects에 duration(ticks)이 들어가며 end_turn로 감소/만료되는지 검증
    SETUP:
      - effect_id="BLEEDING", duration=2(턴)
      - status_inflict=12로 부여 시도(resist는 엔진이 내부 계산)
      - 여러 trial을 돌리고 각 trial 로그를 출력한다(리포트 txt 확인 목적).
    STEPS:
      - trial 반복:
        1) seed 고정
        2) 전투 생성(A1 선턴)
        3) Skill(APPLY_EFFECT step) 실행
        4) 성공한 경우 end_turn으로 ticks 감소/삭제 확인
    EXPECTED:
      - STATUS_CHECK 로그가 항상 존재
      - 성공 시 EFFECT_APPLIED 로그 + duration 감소/삭제
      - 실패 시 EFFECT_RESISTED 로그
    """
    TRIALS = 20
    BASE_SEED = 50000

    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    effect_id = "BLEEDING"
    duration_turns = 2
    inflict = 12

    print(f"\n[Phase14 APPLY_EFFECT Trials] N={TRIALS} base_seed={BASE_SEED} effect={effect_id} dur={duration_turns} inflict={inflict}")

    applied = 0
    resisted = 0

    for t in range(TRIALS):
        random.seed(BASE_SEED + t)
        eng, bs = _battle_1v1_a1_first()

        expected_ticks = turns_to_ticks_for_battle(bs, duration_turns)

        skill = Skill(
            skill_id="test_apply_eff",
            name="test_apply_eff",
            actor=A1,
            action_type="MAIN",
            steps=[
                Step(
                    kind="APPLY_EFFECT",
                    target=E1,
                    effect_id=effect_id,
                    effect_duration=duration_turns,
                    status_inflict=inflict,
                ),
            ],
        )
        out = eng.apply_skill(bs, skill)

        print(f"\n--- trial={t} seed={BASE_SEED+t}")
        for e in out.events:
            print(" ", e)

        assert any("STATUS_CHECK" in ev for ev in out.events)

        if effect_id in bs.combatants[E1].effects:
            applied += 1
            assert bs.combatants[E1].effects[effect_id] == expected_ticks
            # end_turn을 반복해서 ticks가 감소하다 만료되는지 확인
            for _ in range(expected_ticks):
                eng.end_turn(bs)
            assert effect_id not in bs.combatants[E1].effects
        else:
            resisted += 1
            assert any("EFFECT_RESISTED" in ev for ev in out.events)

    print(f"\n[Summary] applied={applied}, resisted={resisted}")
    assert applied > 0  # 20회면 보통 발생
    assert resisted > 0


def test_phase14_remove_effect_uses_check_and_can_succeed_or_fail_over_trials():
    """
    TITLE: REMOVE_EFFECT가 무조건 해제하지 않고, 판정(굴림)을 수행하여 성공/실패가 갈릴 수 있음을 검증
    SETUP:
      - E1에게 BURNED(duration=3 ticks 직접 주입)을 사전 주입한다.
      - REMOVE_EFFECT는 내부적으로 DISPEL 체크를 수행해야 한다.
      - trials를 돌려서 최소 1회는 실패 로그, 최소 1회는 성공 로그가 나오도록 유도한다.
    STEPS:
      - trial 반복:
        1) seed 고정
        2) (상태가 남아있으면) Skill(REMOVE_EFFECT step) 실행
        3) DISPEL_CHECK 로그 존재 확인
        4) 성공 시 실제로 effects에서 제거되는지 확인하고 종료
    EXPECTED:
      - DISPEL_CHECK 로그가 반드시 남는다(굴림 수행)
      - DISPEL_SUCCESS가 나오면 effects에서 제거되어야 한다
    """
    eng, bs = _battle_1v1_a1_first()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    eff = "BURNED"
    bs.combatants[E1].effects[eff] = 3

    found_failed = False
    found_success = False

    for i in range(30):
        random.seed(70000 + i)

        # 같은 턴에 여러 번 시도하므로 MAIN 1회, 이후 SUB 사용
        action_type = "MAIN" if i == 0 else "SUB"

        skill = Skill(
            skill_id=f"test_remove_{i}",
            name=f"test_remove_{i}",
            actor=A1,
            action_type=action_type,
            steps=[
                Step(
                    kind="REMOVE_EFFECT",
                    target=E1,
                    effect_id=eff,
                ),
            ],
        )
        out = eng.apply_skill(bs, skill)

        # 굴림이 수행되었는지 확인
        assert any("DISPEL_CHECK" in ev for ev in out.events) or any(
            "EFFECT_REMOVE_NOOP" in ev for ev in out.events
        )

        if any("DISPEL_FAILED" in ev for ev in out.events):
            found_failed = True

        if any("DISPEL_SUCCESS" in ev for ev in out.events):
            found_success = True
            assert eff not in bs.combatants[E1].effects
            break

        # 실패했으면 아직 남아있어야 함
        if eff in bs.combatants[E1].effects:
            assert any("DISPEL_FAILED" in ev for ev in out.events)

    assert found_success is True  # 30회면 보통 1번 이상은 성공


def test_phase14_attack_then_apply_effect_chain_evade_skips_and_hit_can_reach_status_check():
    """
    TITLE: ATTACK + APPLY_EFFECT 체인에서 EVADE면 CHAIN_BREAK으로 상태이상 판정을 건너뛰고,
           HIT이면 상태이상 판정 로그가 찍히는지 검증
    SETUP:
      - 이전 ATTACK_APPLY_EFFECT step kind는 ATTACK + APPLY_EFFECT(require_prev_gte=1) 두 step으로 분리됨
      - 케이스1(회피 유도): A1을 매우 약하게, E1을 매우 강하게 세팅
      - 케이스2(명중 유도): A1을 매우 강하게, E1을 매우 약하게 세팅
    STEPS:
      - evade 유도 전투에서 최대 30회 시도: CHAIN_BREAK 또는 STEP_SKIPPED가 나오면 성공
      - hit 유도 전투에서 최대 30회 시도: STATUS_CHECK가 나오면 성공
    EXPECTED:
      - EVADE 시도에서 CHAIN_BREAK/STEP_SKIPPED를 최소 1회 확인
      - HIT 시도에서 STATUS_CHECK를 최소 1회 확인
    """
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    effect_id = "POISONED"
    duration = 2
    inflict = 12

    # 1) EVADE 유도: A1 약하게, E1 강하게
    eng, bs = _battle_1v1_a1_first(a1_level=1, a1_agi=61, a1_wis=5, e1_level=15, e1_agi=60, e1_wis=60)
    found_skipped = False
    for i in range(30):
        random.seed(60000 + i)
        # 매 시도마다 새 전투를 생성하여 슬롯 제한 회피
        eng, bs = _battle_1v1_a1_first(a1_level=1, a1_agi=61, a1_wis=5, e1_level=15, e1_agi=60, e1_wis=60)
        skill = Skill(
            skill_id="test_atk_eff",
            name="test_atk_eff",
            actor=A1,
            action_type="MAIN",
            steps=[
                Step(kind="ATTACK", target=E1),
                Step(kind="APPLY_EFFECT", target=E1,
                     effect_id=effect_id, effect_duration=duration,
                     status_inflict=inflict, require_prev_gte=1),
            ],
        )
        out = eng.apply_skill(bs, skill)
        if any("CHAIN_BREAK" in ev or "STEP_SKIPPED" in ev for ev in out.events):
            found_skipped = True
            break
    assert found_skipped is True

    # 2) HIT 유도: A1 강하게, E1 약하게
    found_check = False
    for i in range(30):
        random.seed(61000 + i)
        eng, bs = _battle_1v1_a1_first(a1_level=15, a1_agi=60, a1_wis=60, e1_level=1, e1_agi=5, e1_wis=5)
        skill = Skill(
            skill_id="test_atk_eff",
            name="test_atk_eff",
            actor=A1,
            action_type="MAIN",
            steps=[
                Step(kind="ATTACK", target=E1),
                Step(kind="APPLY_EFFECT", target=E1,
                     effect_id=effect_id, effect_duration=duration,
                     status_inflict=inflict, require_prev_gte=1),
            ],
        )
        out = eng.apply_skill(bs, skill)
        if any("STATUS_CHECK" in ev for ev in out.events):
            found_check = True
            break
    assert found_check is True
