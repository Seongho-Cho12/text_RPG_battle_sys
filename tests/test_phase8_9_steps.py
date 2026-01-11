import random

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Step
from battle_system.engine.engine import BattleEngine


def mk(cid: str, *, team_hint: str, level: int, agi: int, wis: int, atk_range: str = "MELEE") -> CharacterDef:
    """
    TITLE: Phase 8+9 Step 실행 테스트용 캐릭터 생성
    SETUP:
      - 지수 계산이 level/agi/wis를 사용하므로 해당 값만 의미 있게 설정한다.
      - max_hp는 60으로 크게 잡아 반복 실행(trial)에서도 안정적이다.
      - atk_range는 반응공격 후보 산출에 사용되며, 근접(MELEE)만 후보가 된다.
    EXPECTED:
      - BattleEngine.create_battle 입력으로 사용 가능
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=level,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=60,
        basic_attack_range=atk_range,
    )


def _new_battle_for_reactions():
    """
    TITLE: 반응공격이 발생하기 좋은 전투 상태를 만들어 반환
    SETUP:
      - A1(아군)이 E1(적)에게 붙었다가 DISENGAGE하면
        '이동 직전 같은 그룹에 있던 근접 적들'이 반응공격 후보가 된다.
      - 이를 위해 1) ENGAGE, 2) DISENGAGE 순으로 Step을 실행할 예정이다.
    EXPECTED:
      - create_battle 결과 BattleState를 반환
      - A1/E1/E2가 존재하며 E1/E2는 MELEE
    """
    a1 = mk("A1", team_hint="ALLY-", level=5, agi=13, wis=10, atk_range="MELEE")
    e1 = mk("E1", team_hint="ENEMY-", level=5, agi=12, wis=10, atk_range="MELEE")
    e2 = mk("E2", team_hint="ENEMY-", level=5, agi=12, wis=10, atk_range="MELEE")

    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1, e2])
    return eng, bs


def _new_battle_for_attack():
    """
    TITLE: 기본 공격 Step 테스트용 전투 상태를 만들어 반환
    SETUP:
      - A1(아군) vs E1(적) 1:1
    EXPECTED:
      - create_battle 결과 BattleState를 반환
    """
    a1 = mk("A1", team_hint="ALLY-", level=5, agi=13, wis=10, atk_range="MELEE")
    e1 = mk("E1", team_hint="ENEMY-", level=5, agi=12, wis=10, atk_range="MELEE")

    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1])
    return eng, bs


def test_phase8_steps_move_engage_then_disengage_triggers_reactions_trials():
    """
    TITLE: Step 시퀀스(ENGAGE -> DISENGAGE)를 엔진이 실행하고 반응공격 로그가 리포트에 남는지 검증
    SETUP:
      - trial마다 seed를 바꿔가며 여러 번 실행해 결과 분포/로그를 확인한다.
      - reaction_hit_penalty=5를 적용한다.
      - ENGAGE는 반응공격이 발생하지 않을 수도 있지만,
        DISENGAGE는 발생하는 것이 정상(후보가 존재하면).
    STEPS:
      - trial 반복:
        1) random.seed 설정
        2) 전투 생성
        3) steps = [MOVE_ENGAGE(A1->E1), MOVE_DISENGAGE(A1)]
        4) apply_steps 실행
        5) A1 HP 변화와 이벤트를 print로 출력
    EXPECTED:
      - events에 "STEP:" 로그가 존재
      - DISENGAGE 이후 "REACTION:" 로그가 존재 (candidates 또는 none)
      - 출력이 캡처되어 test-result txt에 남는다
    """
    TRIALS = 10
    BASE_SEED = 20000
    PENALTY = 5

    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    print(f"\n[Phase8 Step Trials] trials={TRIALS}, base_seed={BASE_SEED}, reaction_hit_penalty={PENALTY}")
    for t in range(TRIALS):
        seed = BASE_SEED + t
        random.seed(seed)

        eng, bs = _new_battle_for_reactions()
        hp_before = bs.combatants[A1].hp

        steps = [
            Step(kind="MOVE_ENGAGE", actor=A1, target=E1, reaction_immune=False, action_type="MAIN"),
            Step(kind="MOVE_DISENGAGE", actor=A1, target=None, reaction_immune=False, action_type="MAIN"),
        ]

        out = eng.apply_steps(bs, steps, reaction_hit_penalty=PENALTY)
        hp_after = bs.combatants[A1].hp

        print(f"\n--- trial={t} seed={seed} A1_hp {hp_before}->{hp_after}")
        for e in out.events:
            print(" ", e)

        # 최소 검증
        assert any(ev.startswith("STEP:") for ev in out.events)
        assert any(ev.startswith("REACTION:") for ev in out.events)


def test_phase9_steps_attack_runs_trials_and_hp_delta_matches_damage():
    """
    TITLE: Step(ATTACK)을 엔진이 실행하고 로그/HP 변화가 일치하는지 검증(여러 trial)
    SETUP:
      - A1이 E1을 공격한다.
      - 판정은 확률이므로 결과를 고정하지 않고:
        - 로그 형식
        - damage 범위
        - HP 감소량 == damage
        를 확인한다.
    STEPS:
      - trial 반복:
        1) seed 설정
        2) 1:1 전투 생성
        3) apply_steps([ATTACK]) 실행
        4) 이벤트 출력
    EXPECTED:
      - events에 "STEP: ATTACK ..." 로그가 존재
      - damage는 0 또는 {1,3,9}
      - E1 HP 감소량은 damage와 동일
    """
    TRIALS = 10
    BASE_SEED = 21000

    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    allowed_damage = {0, 1, 3, 9}

    print(f"\n[Phase9 Step Trials] trials={TRIALS}, base_seed={BASE_SEED}")
    for t in range(TRIALS):
        seed = BASE_SEED + t
        random.seed(seed)

        eng, bs = _new_battle_for_attack()
        hp_before = bs.combatants[E1].hp

        steps = [
            Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="MAIN"),
        ]

        out = eng.apply_steps(bs, steps)
        hp_after = bs.combatants[E1].hp

        print(f"\n--- trial={t} seed={seed} E1_hp {hp_before}->{hp_after}")
        for e in out.events:
            print(" ", e)

        # "STEP: ATTACK ... dmg=X"에서 dmg 파싱
        attack_logs = [ev for ev in out.events if ev.startswith("STEP: ATTACK")]
        assert len(attack_logs) == 1
        log = attack_logs[0]
        dmg = int(log.split("dmg=")[1])
        assert dmg in allowed_damage
        assert (hp_before - hp_after) == dmg


def test_phase9_steps_combo_move_then_attack_single_main_consumption():
    """
    TITLE: 복합 Step 시퀀스(MOVE_ENGAGE -> ATTACK)를 '한 번의 MAIN 소비'로 실행 가능함을 검증
    SETUP:
      - A1이 E1에게 ENGAGE 후 즉시 ATTACK하는 시나리오를 Step으로 구성한다.
      - apply_steps는 steps[0].action_type으로 슬롯을 1회만 소모한다는 가정이다.
    STEPS:
      1) 전투 생성
      2) A1의 can_main은 True로 시작
      3) apply_steps([MOVE_ENGAGE, ATTACK]) 실행
      4) 실행 후 can_main이 False인지 확인
      5) 같은 턴에서 MAIN을 한 번 더 쓰려 하면 실패해야 한다(선택 검증)
    EXPECTED:
      - 실행 후 A1.can_main == False
      - 이벤트에 MOVE_ENGAGE, ATTACK 로그가 모두 존재
    """
    random.seed(22000)

    eng, bs = _new_battle_for_reactions()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    # 시작 슬롯 상태 확인
    assert bs.combatants[A1].can_main is True

    steps = [
        Step(kind="MOVE_ENGAGE", actor=A1, target=E1, reaction_immune=False, action_type="MAIN"),
        Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="MAIN"),
    ]
    out = eng.apply_steps(bs, steps)

    for e in out.events:
        print(" ", e)

    assert bs.combatants[A1].can_main is False
    assert any("MOVE_ENGAGE" in ev for ev in out.events)
    assert any("ATTACK" in ev for ev in out.events)
