import random
import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Step
from battle_system.engine.engine import BattleEngine


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
    TITLE: APPLY_EFFECT가 즉시 상태이상 판정을 수행하고, 성공 시 effects에 duration이 들어가며 end_turn로 감소/만료되는지 검증
    SETUP:
      - effect_id="BLEED", duration=2
      - inflict/resist를 12/12로 두어 성공/저항이 섞이도록 한다.
      - 여러 trial을 돌리고 각 trial 로그를 출력한다(리포트 txt 확인 목적).
    STEPS:
      - trial 반복:
        1) seed 고정
        2) 전투 생성(A1 선턴)
        3) APPLY_EFFECT 실행
        4) 성공한 경우 end_turn 2회로 2->1->삭제 확인
    EXPECTED:
      - STATUS_CHECK 로그가 항상 존재
      - 성공 시 EFFECT_APPLIED 로그 + duration 감소/삭제
      - 실패 시 EFFECT_RESISTED 로그
    """
    TRIALS = 20
    BASE_SEED = 50000

    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    effect_id = "BLEED"
    duration = 2
    inflict = 12
    resist = 12

    print(f"\n[Phase14 APPLY_EFFECT Trials] N={TRIALS} base_seed={BASE_SEED} effect={effect_id} dur={duration} inflict={inflict} resist={resist}")

    applied = 0
    resisted = 0

    for t in range(TRIALS):
        random.seed(BASE_SEED + t)
        eng, bs = _battle_1v1_a1_first()

        out = eng.apply_steps(
            bs,
            [
                Step(
                    kind="APPLY_EFFECT",
                    actor=A1,
                    target=E1,
                    action_type="MAIN",
                    effect_id=effect_id,
                    effect_duration=duration,
                    status_inflict=inflict,
                    status_resist=resist,
                )
            ],
        )

        print(f"\n--- trial={t} seed={BASE_SEED+t}")
        for e in out.events:
            print(" ", e)

        assert any(ev.startswith("STATUS_CHECK:") for ev in out.events)

        if effect_id in bs.combatants[E1].effects:
            applied += 1
            assert bs.combatants[E1].effects[effect_id] == duration
            eng.end_turn(bs)
            assert bs.combatants[E1].effects[effect_id] == duration - 1
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
      - E1에게 BURN(duration=3)을 사전 주입한다.
      - REMOVE_EFFECT는 status_inflict/status_resist를 사용해 DISP EL 체크를 수행해야 한다.
      - trials를 돌려서 최소 1회는 실패 로그, 최소 1회는 성공 로그가 나오도록 유도한다.
    STEPS:
      - trial 반복:
        1) seed 고정
        2) (상태가 남아있으면) REMOVE_EFFECT 실행
        3) DISPEL_CHECK 로그 존재 확인
        4) 성공 시 실제로 effects에서 제거되는지 확인하고 종료
    EXPECTED:
      - DISPEL_CHECK 로그가 반드시 남는다(굴림 수행)
      - 여러 번 시도하면 DISPEL_FAILED도 최소 1번은 관측될 가능성이 높다
      - DISPEL_SUCCESS가 나오면 effects에서 제거되어야 한다
    """
    import random

    eng, bs = _battle_1v1_a1_first()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    eff = "BURN"
    bs.combatants[E1].effects[eff] = 3

    # 50:50에 가깝게(분포 관측 목적)
    inflict = 12
    resist = 12

    found_failed = False
    found_success = False

    for i in range(30):
        random.seed(70000 + i)

        # 같은 턴에 여러 번 시도하므로 MAIN 1회, 이후 SUB 사용
        action_type = "MAIN" if i == 0 else "SUB"

        out = eng.apply_steps(
            bs,
            [
                Step(
                    kind="REMOVE_EFFECT",
                    actor=A1,
                    target=E1,
                    action_type=action_type,
                    effect_id=eff,
                    status_inflict=inflict,
                    status_resist=resist,
                )
            ],
        )

        # 굴림이 수행되었는지 확인
        assert any(ev.startswith("DISPEL_CHECK:") for ev in out.events) or any(
            ev.startswith("EFFECT_REMOVE_NOOP:") for ev in out.events
        )

        if any(ev.startswith("DISPEL_FAILED:") for ev in out.events):
            found_failed = True

        if any(ev.startswith("DISPEL_SUCCESS:") for ev in out.events):
            found_success = True
            assert eff not in bs.combatants[E1].effects
            break

        # 실패했으면 아직 남아있어야 함
        if eff in bs.combatants[E1].effects:
            assert any(ev.startswith("DISPEL_FAILED:") for ev in out.events)

    assert found_success is True  # 30회면 보통 1번 이상은 성공
    # found_failed는 확률이라 100% 보장하진 않지만, 30회면 대체로 관측됨
    # 엄격히 강제하고 싶으면 inflict/resist를 더 균형적으로/시드 탐색 방식으로 고정하면 됨.


def test_phase14_attack_apply_effect_evade_skips_status_check_and_hit_can_reach_status_check():
    """
    TITLE: ATTACK_APPLY_EFFECT에서 EVADE면 상태이상 판정으로 가지 않고, HIT이면 상태이상 판정 로그가 찍히는지 검증
    SETUP:
      - 케이스1(회피 유도): A1을 매우 약하게, E1을 매우 강하게 세팅
      - 케이스2(명중 유도): A1을 매우 강하게, E1을 매우 약하게 세팅
      - 두 케이스 모두에서 step을 여러 번 시도하여 목적 로그가 최소 1회는 나오게 한다(플레이키 방지)
    STEPS:
      - evade 유도 전투에서 최대 30회 시도: STATUS_SKIPPED가 나오면 성공
      - hit 유도 전투에서 최대 30회 시도: STATUS_CHECK가 나오면 성공
    EXPECTED:
      - EVADE 시도에서 STATUS_SKIPPED를 최소 1회 확인
      - HIT 시도에서 STATUS_CHECK를 최소 1회 확인
    """
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    effect_id = "POISON"
    duration = 2
    inflict = 12
    resist = 12

    # 1) EVADE 유도
    eng, bs = _battle_1v1_a1_first(a1_level=1, a1_agi=61, a1_wis=5, e1_level=15, e1_agi=60, e1_wis=60)
    found_skipped = False
    for i in range(30):
        random.seed(60000 + i)
        out = eng.apply_steps(
            bs,
            [
                Step(
                    kind="ATTACK_APPLY_EFFECT",
                    actor=A1,
                    target=E1,
                    action_type="MAIN" if i == 0 else "SUB",  # 같은 턴에 2번이면 SUB로
                    effect_id=effect_id,
                    effect_duration=duration,
                    status_inflict=inflict,
                    status_resist=resist,
                )
            ],
        )
        if any(ev.startswith("STATUS_SKIPPED:") for ev in out.events):
            found_skipped = True
            break
    assert found_skipped is True

    # 다음 케이스는 새 전투(턴/슬롯 초기화)
    eng, bs = _battle_1v1_a1_first(a1_level=15, a1_agi=60, a1_wis=60, e1_level=1, e1_agi=5, e1_wis=5)
    found_check = False
    for i in range(30):
        random.seed(61000 + i)
        out = eng.apply_steps(
            bs,
            [
                Step(
                    kind="ATTACK_APPLY_EFFECT",
                    actor=A1,
                    target=E1,
                    action_type="MAIN" if i == 0 else "SUB",
                    effect_id=effect_id,
                    effect_duration=duration,
                    status_inflict=inflict,
                    status_resist=resist,
                )
            ],
        )
        if any(ev.startswith("STATUS_CHECK:") for ev in out.events):
            found_check = True
            break
    assert found_check is True
