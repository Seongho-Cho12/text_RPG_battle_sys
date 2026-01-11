# battle_system/timebase/durations.py
from __future__ import annotations

from battle_system.core.models import BattleState


def turns_to_ticks(turns: int, *, participant_count: int) -> int:
    """
    (턴) -> (tick)
    규칙: ticks = turns * participant_count + 1
    """
    t = int(turns)
    if t < 0:
        raise ValueError("turns must be >= 0")
    n = int(participant_count)
    if n <= 0:
        raise ValueError("participant_count must be >= 1")
    return t * n + 1


def ticks_to_turns(ticks: int, *, participant_count: int) -> int:
    """
    (tick) -> (턴) (표시용)
    규칙: turns = ticks // participant_count

    - 사용자에게 보여줄 때 "대략 몇 턴 남음"을 의미.
    - +1 오프셋은 무시하고 단순 정수 나눗셈으로 표기한다(요구사항).
    """
    x = int(ticks)
    if x < 0:
        x = 0
    n = int(participant_count)
    if n <= 0:
        raise ValueError("participant_count must be >= 1")
    return x // n


def turns_to_ticks_for_battle(bs: BattleState, turns: int) -> int:
    return turns_to_ticks(turns, participant_count=len(bs.turn_order))


def ticks_to_turns_for_battle(bs: BattleState, ticks: int) -> int:
    return ticks_to_turns(ticks, participant_count=len(bs.turn_order))
