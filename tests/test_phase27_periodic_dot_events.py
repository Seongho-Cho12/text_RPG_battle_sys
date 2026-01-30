from __future__ import annotations

from typing import List, Tuple

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.timebase.durations import turns_to_ticks_for_battle


# -------------------------
# helpers
# -------------------------

def _mk_char(cid: str, *, level: int = 1, max_hp: int = 50) -> CharacterDef:
    return CharacterDef(
        cid=CombatantID(cid),
        name=cid,
        level=level,
        stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10),
        max_hp=max_hp,
    )


def _mk_engine_2v2() -> Tuple[BattleEngine, object, CombatantID]:
    """
    participant_count=4 고정(2v2).
    turn_boundary (tick % 4 == 1) 검증을 안정적으로 하기 위함.
    """
    eng = BattleEngine()
    a = _mk_char("A")
    b = _mk_char("B")
    e = _mk_char("E")
    f = _mk_char("F")
    bs = eng.create_battle([a, b], [e, f])
    assert len(bs.turn_order) == 4
    return eng, bs, CombatantID("E")


def _events(outcome) -> List[str]:
    return list(getattr(outcome, "events", []))


def _run_end_turns(eng: BattleEngine, bs, n: int) -> List[str]:
    out: List[str] = []
    for _ in range(n):
        r = eng.end_turn(bs)
        out.extend(_events(r))
    return out


def _dot_events_for(events: List[str], *, cid: CombatantID, effect: str) -> List[str]:
    prefix = f"DOT_TICK:"
    key_cid = f" cid={cid} "
    key_eff = f" effect={effect} "
    return [e for e in events if e.startswith(prefix) and key_cid in e and key_eff in e]


def _dot_events_any(events: List[str]) -> List[str]:
    return [e for e in events if e.startswith("DOT_TICK:")]


# -------------------------
# Phase 27 tests
# -------------------------

def test_phase27_bleeding_1turn_triggers_twice_on_tick_1_and_5_and_then_stops():
    """
    [Phase 27] Bleeding(턴 기반 DoT)
    - n=4, turn_boundary: tick % 4 == 1  => tick=1,5,9,...
    - duration(1 turn) => turns_to_ticks = 1*4+1 = 5 ticks
    기대:
    - tick=1에서 1회 발동
    - tick=5에서 1회 발동
    => 총 2회 dmg=1
    - 5틱 이후에는 더 이상 발동 없음
    """
    eng, bs, victim = _mk_engine_2v2()

    bs.combatants[victim].effects["Bleeding"] = turns_to_ticks_for_battle(bs, 1)  # 5
    hp0 = bs.combatants[victim].hp

    events_5 = _run_end_turns(eng, bs, 5)
    hp1 = bs.combatants[victim].hp

    ev_bleed = _dot_events_for(events_5, cid=victim, effect="Bleeding")
    assert len(ev_bleed) == 2
    assert hp1 == hp0 - 2

    # 추가로 4틱(다음 boundary tick=9)까지 진행해도 Bleeding은 만료됐으니 0회
    events_more = _run_end_turns(eng, bs, 4)
    ev_bleed_more = _dot_events_for(events_more, cid=victim, effect="Bleeding")
    assert len(ev_bleed_more) == 0
    assert bs.combatants[victim].hp == hp1


def test_phase27_poisoned_2turn_triggers_three_times_on_tick_1_5_9_and_then_stops():
    """
    [Phase 27] Poisoned(턴 기반 DoT)
    - n=4, boundary tick=1,5,9...
    - duration(2 turn) => ticks = 2*4+1 = 9
    기대:
    - tick=1,5,9 총 3회 발동 => 총 -3
    - 이후 발동 없음
    """
    eng, bs, victim = _mk_engine_2v2()

    bs.combatants[victim].effects["Poisoned"] = turns_to_ticks_for_battle(bs, 2)  # 9
    hp0 = bs.combatants[victim].hp

    events_9 = _run_end_turns(eng, bs, 9)
    hp1 = bs.combatants[victim].hp

    ev = _dot_events_for(events_9, cid=victim, effect="Poisoned")
    assert len(ev) == 3
    assert hp1 == hp0 - 3

    # 만료 후 추가 진행해도 발동 없음
    events_more = _run_end_turns(eng, bs, 4)
    assert len(_dot_events_for(events_more, cid=victim, effect="Poisoned")) == 0
    assert bs.combatants[victim].hp == hp1


def test_phase27_decay_tick_based_triggers_on_tick_1_4_7_for_7ticks_and_then_stops():
    """
    [Phase 27] Decay(틱 기반 DoT)
    - decay_boundary: tick % 3 == 1 => tick=1,4,7,10,...
    - duration=7 ticks로 직접 부여
    기대:
    - tick=1,4,7 총 3회 발동, 각 dmg=2 => 총 -6
    - 이후 발동 없음
    """
    eng, bs, victim = _mk_engine_2v2()

    bs.combatants[victim].effects["Decay"] = 7
    hp0 = bs.combatants[victim].hp

    events_7 = _run_end_turns(eng, bs, 7)
    hp1 = bs.combatants[victim].hp

    ev = _dot_events_for(events_7, cid=victim, effect="Decay")
    assert len(ev) == 3
    assert hp1 == hp0 - 6

    # 만료 후 추가 진행해도 발동 없음
    events_more = _run_end_turns(eng, bs, 3)
    assert len(_dot_events_for(events_more, cid=victim, effect="Decay")) == 0
    assert bs.combatants[victim].hp == hp1


def test_phase27_combined_effects_emit_separate_log_lines_and_damage_stacks():
    """
    [Phase 27] 복합 DoT
    - Bleeding(턴 기반) + Poisoned(턴 기반) + Decay(틱 기반)
    - tick=1은 (turn_boundary, decay_boundary) 둘 다 만족 (n=4 기준)
    기대:
    - tick=1에서 victim에 대해 DOT 로그가 3줄(효과별) 발생
    - hp는 총 (1 + 1 + 2) = 4 감소
    """
    eng, bs, victim = _mk_engine_2v2()

    bs.combatants[victim].effects["Bleeding"] = turns_to_ticks_for_battle(bs, 1)  # 5
    bs.combatants[victim].effects["Poisoned"] = turns_to_ticks_for_battle(bs, 1)  # 5
    bs.combatants[victim].effects["Decay"] = 7

    hp0 = bs.combatants[victim].hp

    # 1틱만 진행 => tick=1
    events = _run_end_turns(eng, bs, 1)
    hp1 = bs.combatants[victim].hp

    ev_b = _dot_events_for(events, cid=victim, effect="Bleeding")
    ev_p = _dot_events_for(events, cid=victim, effect="Poisoned")
    ev_d = _dot_events_for(events, cid=victim, effect="Decay")

    assert len(ev_b) == 1
    assert len(ev_p) == 1
    assert len(ev_d) == 1
    assert hp1 == hp0 - 4


def test_phase27_no_dot_events_when_no_effects_present():
    """
    [Phase 27] 어떤 대상도 DoT 효과가 없으면 DOT_TICK 로그가 없어야 한다.
    """
    eng, bs, _ = _mk_engine_2v2()
    events = _run_end_turns(eng, bs, 10)
    assert len(_dot_events_any(events)) == 0
