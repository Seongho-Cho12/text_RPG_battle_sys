from __future__ import annotations
import random
from dataclasses import dataclass


# ==============================
# ⚠ 밸런스 조절은 여기서만 ⚠
# ==============================
# 아래 함수들이 받는 지수(hit/eva/weak/strong/crit)는
# "어떻게 계산하느냐"가 게임 밸런스의 핵심입니다.
# 지금은 공격/스킬에서 계산된 지수를 그대로 받도록 만들었습니다.
# 나중에 공식이 확정되면, 공격/스킬 쪽에서 지수 계산부만 바꾸면 됩니다.


@dataclass(frozen=True)
class HitResult:
    outcome: str  # "HIT" | "EVADE"
    roll: int
    hit_index: int
    evade_index: int
    total: int


@dataclass(frozen=True)
class CritResult:
    outcome: str  # "WEAK" | "STRONG" | "CRITICAL"
    roll: int
    weak_index: int
    strong_index: int
    crit_index: int
    total: int

@dataclass(frozen=True)
class StatusCheckResult:
    """
    상태이상/저항 판정 결과.

    success=True  : 상태이상 부여 성공(= 저항 실패)
    success=False : 상태이상 부여 실패(= 저항 성공)
    roll          : 1..(inflict+resist) 범위에서 뽑힌 값
    """
    success: bool
    roll: int


def hit_check(*, hit_index: int, evade_index: int) -> HitResult:
    """
    명중 판정 (가중치 추첨)

    규칙:
      - roll ~ UniformInt[1, hit_index + evade_index]
      - roll <= hit_index                 -> HIT
      - hit_index < roll <= hit_index+evade_index -> EVADE

    주의:
      - hit_index, evade_index는 0 이상 정수여야 함.
      - 둘 다 0이면 판정 불가(설계 오류)라 예외.
    """
    if hit_index < 0 or evade_index < 0:
        raise ValueError("hit_index and evade_index must be >= 0")
    total = hit_index + evade_index
    if total <= 0:
        raise ValueError("hit_index + evade_index must be > 0")

    roll = random.randint(1, total)
    outcome = "HIT" if roll <= hit_index else "EVADE"
    return HitResult(
        outcome=outcome,
        roll=roll,
        hit_index=hit_index,
        evade_index=evade_index,
        total=total,
    )


def crit_check(*, weak_index: int, strong_index: int, crit_index: int) -> CritResult:
    """
    치명/공격 강도 판정 (가중치 추첨) — NONE 없음

    규칙:
      - roll ~ UniformInt[1, weak + strong + crit]
      - roll <= weak                               -> WEAK
      - weak < roll <= weak + strong               -> STRONG
      - weak + strong < roll <= weak+strong+crit   -> CRITICAL

    주의:
      - 세 지수는 0 이상 정수여야 함.
      - 합이 0이면 판정 불가(설계 오류)라 예외.
    """
    if weak_index < 0 or strong_index < 0 or crit_index < 0:
        raise ValueError("indices must be >= 0")
    total = weak_index + strong_index + crit_index
    if total <= 0:
        raise ValueError("weak+strong+crit must be > 0")

    roll = random.randint(1, total)
    if roll <= weak_index:
        outcome = "WEAK"
    elif roll <= weak_index + strong_index:
        outcome = "STRONG"
    else:
        outcome = "CRITICAL"

    return CritResult(
        outcome=outcome,
        roll=roll,
        weak_index=weak_index,
        strong_index=strong_index,
        crit_index=crit_index,
        total=total,
    )

def roll_status_success(*, inflict: int, resist: int, rng: random.Random | None = None) -> StatusCheckResult:
    """
    상태이상 판정(가중치 추첨):

    - roll은 1..(inflict+resist) 사이 정수
    - roll <= inflict 이면 부여 성공
    - 그 외는 저항 성공(부여 실패)
    """
    if inflict < 0 or resist < 0:
        raise ValueError("inflict/resist must be >= 0")
    if inflict == 0 and resist == 0:
        raise ValueError("inflict+resist must be > 0")

    r = rng or random
    total = inflict + resist
    roll = r.randint(1, total)
    return StatusCheckResult(success=(roll <= inflict), roll=roll)
