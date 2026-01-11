from __future__ import annotations

from dataclasses import dataclass
from battle_system.core.models import Stats


HIT_BASE: int = 40  # 밸런싱 시 여기만 바꾸면 됨


@dataclass(frozen=True)
class HitIndices:
    """
    명중 판정에서 사용하는 '명중/회피 지수' 묶음.
    checks.roll_hit_success(hit, evade)를 돌릴 때 그대로 넣으면 됨.
    """
    hit: int
    evade: int


def compute_hit_index(level: int) -> int:
    """
    시전자 명중 지수
    - 40 + level
    """
    return int(HIT_BASE + level)


def compute_evade_index(stats: Stats) -> int:
    """
    피격자 회피 지수
    - AGI/WIS 두 값으로 계산
    - {(max*2 + min)}/3

    NOTE: 정수 지수로 굴리기 때문에 반올림 방식이 중요함.
          여기서는 int()로 내림 처리. (원하면 round로 바꾸면 됨)
    """
    a = int(stats.agi)
    w = int(stats.wis)
    hi = a if a >= w else w
    lo = w if a >= w else a
    return int((hi * 2 + lo) / 3)


def compute_hit_indices(attacker_level: int, defender_stats: Stats) -> HitIndices:
    """
    명중 판정용 최종 지수 계산.
    """
    return HitIndices(
        hit=compute_hit_index(attacker_level),
        evade=compute_evade_index(defender_stats),
    )
