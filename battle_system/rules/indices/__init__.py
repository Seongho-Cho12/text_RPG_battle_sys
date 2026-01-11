from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from battle_system.core.models import Stats

# balance.py의 기본값을 그대로 가져옴 :contentReference[oaicite:1]{index=1}
BASE_WEAK = 20.0
BASE_STRONG = 0.0
BASE_CRIT = 0.0

# 스킬의 "보정 스탯"
CritStat = Literal["STR", "AGI", "INT", "WIS"]


@dataclass(frozen=True)
class CritIndices:
    """
    치명(약/강/치명타) 판정에서 사용하는 지수.
    checks.roll_crit_outcome(weak, strong, crit)을 돌릴 때 그대로 넣으면 됨.
    """
    weak: int
    strong: int
    crit: int


def level_to_rarity(level: int) -> str:
    """
    레벨 -> 희귀도(공식 선택) 매핑
    1~3:  고물
    4~8:  일반
    9~12: 언커먼
    13~16: 레어
    17~19: 진귀
    20~:  전설
    """
    if level <= 3:
        return "고물"
    if level <= 8:
        return "일반"
    if level <= 12:
        return "언커먼"
    if level <= 16:
        return "레어"
    if level <= 19:
        return "진귀"
    return "전설"


def _clamp_nonneg(x: float) -> float:
    return x if x > 0 else 0.0


def _calc_strength_like(rarity: str, primary: float) -> tuple[float, float, float]:
    """
    '근력 무기' 방식(STR/INT 공통): (약공, 강공, 치명) 지수

    balance.py의 _calc_strength_weapon에서
    - STR만 primary로 치환
    - AGI는 사용하지 않으므로 입력에서 제거한 버전
    """
    if rarity == "고물":
        weak = BASE_WEAK + (20 - primary)
        strong = BASE_STRONG + (primary / 2)
        crit = BASE_CRIT + 0
    elif rarity == "일반":
        weak = BASE_WEAK + (17 - primary)
        strong = BASE_STRONG + (primary)
        crit = BASE_CRIT + (primary / 5)
    elif rarity == "언커먼":
        weak = BASE_WEAK + (14 - primary)
        strong = BASE_STRONG + (primary * 1.5)
        crit = BASE_CRIT + (primary / 3)
    elif rarity == "레어":
        weak = BASE_WEAK + (11 - primary)
        strong = BASE_STRONG + (primary * 2)
        crit = BASE_CRIT + (primary / 2)
    elif rarity == "진귀":
        weak = BASE_WEAK + (8 - primary)
        strong = BASE_STRONG + (primary * 3)
        crit = BASE_CRIT + (primary)
    elif rarity == "전설":
        weak = BASE_WEAK + (5 - primary)
        strong = BASE_STRONG + (primary * 3.5)
        crit = BASE_CRIT + (primary * 1.5)
    else:
        raise ValueError("알 수 없는 희귀도")
    return weak, strong, crit


def _calc_agility_like(rarity: str, primary: float, secondary: float) -> tuple[float, float, float]:
    """
    '민첩 무기' 방식(AGI/WIS 공통): (약공, 강공, 치명) 지수

    balance.py의 _calc_agility_weapon에서
    - AGI -> primary
    - STR -> secondary

    WIS 타입은 (primary=WIS, secondary=INT)로 들어오게 됨.
    """
    if rarity == "고물":
        weak = BASE_WEAK + (30 - primary)
        strong = BASE_STRONG + (primary / 2) + (secondary / 4)
        crit = BASE_CRIT + 0
    elif rarity == "일반":
        weak = BASE_WEAK + (27 - primary)
        strong = BASE_STRONG + (primary) + (secondary / 3)
        crit = BASE_CRIT + (primary / 4) + (secondary / 5)
    elif rarity == "언커먼":
        weak = BASE_WEAK + (25 - primary)
        strong = BASE_STRONG + (primary * 1.5) + (secondary / 2)
        crit = BASE_CRIT + (primary / 2) + (secondary / 5)
    elif rarity == "레어":
        weak = BASE_WEAK + (23 - primary)
        strong = BASE_STRONG + (primary * 2) + (secondary / 2)
        crit = BASE_CRIT + (primary) + (secondary / 5)
    elif rarity == "진귀":
        weak = BASE_WEAK + (20 - primary)
        strong = BASE_STRONG + (primary * 2) + (secondary / 2)
        crit = BASE_CRIT + (primary * 1.2) + (secondary / 5)
    elif rarity == "전설":
        weak = BASE_WEAK + (18 - primary)
        strong = BASE_STRONG + (primary * 2.5) + (secondary / 2)
        crit = BASE_CRIT + (primary * 1.8) + (secondary / 5)
    else:
        raise ValueError("알 수 없는 희귀도")
    return weak, strong, crit


def compute_crit_indices(*, attacker_level: int, attacker_stats: Stats, crit_stat: CritStat) -> CritIndices:
    """
    치명(약/강/치명타) 지수 계산.

    crit_stat 규칙(요구사항 반영):
    - STR: 근력 무기 공식, primary=STR
    - INT: 근력 무기 공식, primary=INT (STR 대신 INT)
    - AGI: 민첩 무기 공식, primary=AGI, secondary=STR
    - WIS: 민첩 무기 공식, primary=WIS, secondary=INT (AGI 대신 WIS, STR 대신 INT)

    희귀도는 '레벨 구간'으로 선택.
    """
    rarity = level_to_rarity(attacker_level)

    if crit_stat == "STR":
        w, s, c = _calc_strength_like(rarity, primary=float(attacker_stats.str))
    elif crit_stat == "INT":
        w, s, c = _calc_strength_like(rarity, primary=float(attacker_stats.int))
    elif crit_stat == "AGI":
        w, s, c = _calc_agility_like(
            rarity,
            primary=float(attacker_stats.agi),
            secondary=float(attacker_stats.str),
        )
    elif crit_stat == "WIS":
        w, s, c = _calc_agility_like(
            rarity,
            primary=float(attacker_stats.wis),
            secondary=float(attacker_stats.int),
        )
    else:
        raise ValueError(f"Unknown crit_stat: {crit_stat}")

    # 지수는 음수면 0으로 클램핑(기존 balance.py와 동일 철학)
    w = _clamp_nonneg(w)
    s = _clamp_nonneg(s)
    c = _clamp_nonneg(c)

    # checks에서 정수 지수로 굴릴 예정이라 int()로 변환(내림).
    return CritIndices(weak=int(w), strong=int(s), crit=int(c))
