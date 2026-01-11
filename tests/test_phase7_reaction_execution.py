import random

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.engine.engine import BattleEngine
from battle_system.formation.movement import engage, disengage
from battle_system.formation.reactions import reaction_attack_candidates
from battle_system.rules.basic_attack import execute_reaction_attacks


def mk(cid: str, *, team_hint: str, level: int, agi: int, wis: int, atk_range: str = "MELEE") -> CharacterDef:
    """
    TITLE: Phase 7 반응공격 실행 테스트용 캐릭터 생성 헬퍼
    SETUP:
      - level/agi/wis만 의미 있게 사용
      - max_hp는 50으로 크게 잡아 여러 번 테스트해도 안전
      - basic_attack_range는 반응공격 후보 산출에 사용(MELEE만 후보)
    EXPECTED:
      - create_battle 입력으로 바로 사용 가능
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=level,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=50,
        basic_attack_range=atk_range,
    )


def _setup_battle():
    """
    TITLE: Phase 7 공통 시나리오 세팅(ENGAGE로 붙인 뒤 DISENGAGE로 이탈)
    SETUP:
      - A1(ALLY) 이동자
      - E1/E2(ENEMY) 근접
      - 반응공격이 성립하려면 이동 전에 같은 그룹이어야 하므로:
        ENGAGE(A1,E1)로 같은 그룹을 만든 뒤 DISENGAGE(A1)로 이탈시킨다.
    EXPECTED:
      - 반환: (bs, A1, candidates, prev_gid)
      - candidates == {E1,E2}
    """
    a1 = mk("A1", team_hint="ALLY-", level=5, agi=10, wis=10, atk_range="MELEE")
    e1 = mk("E1", team_hint="ENEMY-", level=5, agi=12, wis=10, atk_range="MELEE")
    e2 = mk("E2", team_hint="ENEMY-", level=5, agi=12, wis=10, atk_range="MELEE")

    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1, e2])

    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    engage(bs, actor=A1, target=E1)
    prev_gid = bs.combatants[A1].group_id
    disengage(bs, actor=A1)

    cands = reaction_attack_candidates(
        bs,
        mover=A1,
        prev_group_id=prev_gid,
        reaction_immune=False,
    )
    assert set(cands) == {CombatantID("E1"), CombatantID("E2")}
    return bs, A1, cands, prev_gid


def test_phase7_reaction_attacks_multiple_trials_and_log_to_report():
    """
    TITLE: 반응공격을 여러 번 실행하며(여러 seed) 결과를 출력하고 리포트 파일에서 확인 가능하게 한다
    SETUP:
      - 동일한 시나리오를 매 trial마다 새로 구성하여 누적 영향을 제거한다.
      - 각 trial에서 random.seed를 다르게 설정한다(재현 가능 + 결과 다양).
      - penalty=5로 반응공격 명중 페널티 적용.
    STEPS:
      1) trial을 N회 반복
      2) 매 trial:
         - seed 설정
         - battle 세팅(ENGAGE -> DISENGAGE)
         - execute_reaction_attacks 실행
         - 결과(outcome/damage/HP변화)를 print로 남김
      3) 전체 통계(총 hit 수, 총 damage)도 print로 남김
    EXPECTED:
      - 매 trial 결과 dict의 키는 항상 {E1,E2}
      - outcome은 EVADE/WEAK/STRONG/CRITICAL 중 하나
      - damage는 0 또는 {1,3,9}
      - 출력이 conftest에 의해 test-result/...txt에 포함된다
    """
    TRIALS = 20
    BASE_SEED = 9000
    PENALTY = 5

    allowed_outcomes = {"EVADE", "WEAK", "STRONG", "CRITICAL"}
    allowed_damages = {0, 1, 3, 9}

    total_hits = 0
    total_damage = 0

    print(f"\n[Phase7 Trials] trials={TRIALS}, base_seed={BASE_SEED}, reaction_hit_penalty={PENALTY}")
    print("trial | seed | A1_hp_before->after | E1(outcome,damage) | E2(outcome,damage)")

    for t in range(TRIALS):
        seed = BASE_SEED + t
        random.seed(seed)

        bs, A1, cands, _prev_gid = _setup_battle()
        hp_before = bs.combatants[A1].hp

        results = execute_reaction_attacks(
            bs,
            mover=A1,
            candidates=cands,
            reaction_hit_penalty=PENALTY,
        )

        assert set(results.keys()) == set(cands)

        hp_after = bs.combatants[A1].hp
        line_parts = [f"{t:>5} | {seed} | {hp_before}->{hp_after}"]

        # 결과 검증 + 통계
        for attacker in sorted(results.keys(), key=lambda x: str(x)):
            r = results[attacker]
            assert r["outcome"] in allowed_outcomes
            assert r["damage"] in allowed_damages

            if r["outcome"] == "EVADE":
                assert r["hit"] is False and r["damage"] == 0
            else:
                assert r["hit"] is True and r["damage"] in {1, 3, 9}
                total_hits += 1

            total_damage += r["damage"]
            line_parts.append(f"{str(attacker)}({r['outcome']},{r['damage']})")

        print(" | ".join(line_parts))

    print(f"\n[Summary] total_hits={total_hits} (out of {TRIALS*2} attacks), total_damage={total_damage}")
