import random
from battle_system.rules.checks import roll_status_success


def test_phase13_status_check_trials_and_log_distribution():
    """
    TITLE: 상태이상 판정(가중치 추첨)이 여러 trial에서 정상적으로 분포를 만들고 로그로 남는지 검증
    SETUP:
      - 같은 trial 수로 inflict/resist 비율을 바꾼 3가지 케이스를 실행한다.
        A) inflict 10, resist 10  -> 성공률 ~50%
        B) inflict 20, resist 5   -> 성공률 상승
        C) inflict 5,  resist 20  -> 성공률 하락
      - seed를 고정하여 재현 가능하게 한다.
    STEPS:
      1) 각 케이스별로 N회 roll_status_success 실행
      2) success 횟수/비율을 계산
      3) 상세 로그(첫 10개 roll)와 요약을 print
    EXPECTED:
      - 성공 비율이 B > A > C 순서로 나와야 한다(방향성 확인)
      - 모든 roll은 1..(inflict+resist) 범위여야 한다
      - 출력이 캡처되어 test-result txt에서 확인 가능해야 한다
    """
    N = 200
    BASE_SEED = 30000

    cases = [
        ("A", 10, 10),
        ("B", 20, 5),
        ("C", 5, 20),
    ]

    results = {}

    print(f"\n[Phase13 StatusCheck Trials] N={N}, base_seed={BASE_SEED}")
    for idx, (name, inflict, resist) in enumerate(cases):
        rng = random.Random(BASE_SEED + idx)

        succ = 0
        first_rolls = []
        for i in range(N):
            r = roll_status_success(inflict=inflict, resist=resist, rng=rng)
            if i < 10:
                first_rolls.append((r.roll, r.success))
            if r.success:
                succ += 1
            assert 1 <= r.roll <= (inflict + resist)

        rate = succ / N
        results[name] = rate

        print(f"\nCASE {name}: inflict={inflict}, resist={resist}")
        print("first10 (roll,success):", first_rolls)
        print(f"success={succ}/{N} rate={rate:.3f}")

    print("\n[Summary rates]", results)

    # 방향성 검증(통계적 엄밀성 X, N=200이면 보통 충분히 안정)
    assert results["B"] > results["A"] > results["C"]
