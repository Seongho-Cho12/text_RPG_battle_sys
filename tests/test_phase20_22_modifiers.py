from __future__ import annotations

import os
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.types import CombatantID
from battle_system.core.commands import Step, Skill
from battle_system.timebase.durations import turns_to_ticks_for_battle


def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int = 50) -> CharacterDef:
    # ✅ name 필수 반영
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_test_result(test_file: str, test_name: str, doc: str, body: str) -> Path:
    out_dir = Path("test-result")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    out_path = out_dir / f"{stamp}_{Path(test_file).name}.txt"
    content = (
        "============================================================\n"
        f"[PASS] {test_file}::{test_name}\n"
        "------------------------------------------------------------\n"
        "[EXPERIMENT]\n"
        f"{doc.strip()}\n\n"
        "[CAPTURED OUTPUT]\n"
        f"{body.rstrip()}\n"
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path


def test_phase20_1_modifier_turns_to_ticks_saved_correctly_and_appended(capsys, request):
    """
    TITLE: APPLY_MODIFIER가 (턴)->(tick) 변환 후 modifiers 리스트에 append 되는지 검증
        PURPOSE:
          - modifier_duration(턴)을 입력하면 turns_to_ticks_for_battle 규칙으로 tick으로 변환되어 저장되어야 한다.
          - 같은 key/delta라도 "merge/연장"이 아니라 항상 새 ModifierInstance가 append 되어야 한다(중첩 기본 정책).
        SETUP:
          - 2인 전투(A1 vs E1)로 참여 인원=2 고정.
          - APPLY_MODIFIER를 E1에게 2턴 부여.
          - 기대 tick: 2*2+1 = 5
        EXPECTED:
          - modifiers 길이: 1
          - key='HIT', delta=7
          - ticks_left=5
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    turns = 2
    expected_ticks = turns_to_ticks_for_battle(bs, turns)
    assert expected_ticks == 5

    skill = Skill(
        skill_id="test_mod_1",
        name="test_mod_1",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_MODIFIER",
                target=tgt,
                modifier_key="HIT",
                modifier_delta=7,
                modifier_duration=turns,  # ✅ 턴
            )
        ],
    )
    out = eng.apply_skill(bs, skill)

    mods = bs.combatants[tgt].modifiers
    print("\n[Phase20-1] MOD_APPLIED events:")
    for e in out.events:
        print(" ", e)

    assert len(mods) == 1
    assert mods[0].key == "HIT"
    assert mods[0].delta == 7
    assert mods[0].ticks_left == expected_ticks

    captured = capsys.readouterr().out
    _write_test_result(__file__, request.node.name, inspect.getdoc(test_phase20_1_modifier_turns_to_ticks_saved_correctly_and_appended) or "", captured)


def test_phase20_2_end_turn_decrements_modifier_ticks_and_deletes_at_zero(capsys, request):
    """
    TITLE: end_turn마다 modifier tick이 감소하고 0이 되면 삭제되는지 검증
        PURPOSE:
          - end_turn() 호출마다 모든 참가자의 modifiers.ticks_left가 1씩 감소해야 한다.
          - ticks_left가 0 이하가 되면 해당 modifier 인스턴스가 리스트에서 제거되어야 한다.
        EXPECTED:
          - 적용 직후 len=1, ticks_left=1
          - end_turn 1회 후 len=0
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    turns = 0
    expected_ticks = turns_to_ticks_for_battle(bs, turns)
    assert expected_ticks == 1

    skill = Skill(
        skill_id="test_mod_2",
        name="test_mod_2",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_MODIFIER",
                target=tgt,
                modifier_key="WEAK",
                modifier_delta=-3,
                modifier_duration=turns,
            )
        ],
    )
    out = eng.apply_skill(bs, skill)

    mods = bs.combatants[tgt].modifiers
    assert len(mods) == 1
    assert mods[0].ticks_left == 1

    print("\n[Phase20-2] Before end_turn: ticks_left =", mods[0].ticks_left)
    for e in out.events:
        print(" ", e)

    eng.end_turn(bs)

    mods_after = bs.combatants[tgt].modifiers
    print("[Phase20-2] After end_turn: modifiers_len =", len(mods_after))
    assert len(mods_after) == 0

    captured = capsys.readouterr().out
    _write_test_result(__file__, request.node.name, inspect.getdoc(test_phase20_2_end_turn_decrements_modifier_ticks_and_deletes_at_zero) or "", captured)


def test_phase21_22_modifier_stacks_as_distinct_instances_not_duration_extend(capsys, request):
    """
    TITLE: 동일 key/delta라도 modifier는 "tick 연장"이 아니라 인스턴스로 중첩되는지 검증
        PURPOSE:
          - 동일한 modifier를 다시 받았을 때, 기존 인스턴스 ticks_left를 늘리는 방식(연장)이 아니라
            "새 인스턴스 append"로 중첩되어야 한다.
        EXPECTED:
          - len=2
          - ticks_left 집합이 {1,3} 형태로 나타남(순서는 무관)
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    turns = 1
    ticks = turns_to_ticks_for_battle(bs, turns)
    assert ticks == 3

    skill1 = Skill(
        skill_id="test_mod_stack_1",
        name="test_mod_stack_1",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_MODIFIER",
                target=tgt,
                modifier_key="HIT",
                modifier_delta=5,
                modifier_duration=turns,
            )
        ],
    )
    out1 = eng.apply_skill(bs, skill1)

    assert len(bs.combatants[tgt].modifiers) == 1
    assert bs.combatants[tgt].modifiers[0].ticks_left == 3

    # end_turn 2회: A1 턴 -> E1 턴 -> A1 턴
    eng.end_turn(bs)
    eng.end_turn(bs)
    assert bs.current_actor_id() == CombatantID("A1")

    # 이 시점에 기존 modifier는 2 감소되어 ticks_left=1
    assert len(bs.combatants[tgt].modifiers) == 1
    assert bs.combatants[tgt].modifiers[0].ticks_left == 1

    skill2 = Skill(
        skill_id="test_mod_stack_2",
        name="test_mod_stack_2",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_MODIFIER",
                target=tgt,
                modifier_key="HIT",
                modifier_delta=5,
                modifier_duration=turns,
            )
        ],
    )
    out2 = eng.apply_skill(bs, skill2)

    mods = bs.combatants[tgt].modifiers
    assert len(mods) == 2
    ticks_lefts = sorted([m.ticks_left for m in mods])

    print("\n[Phase21-22] reapply stacks (no extend)")
    print("ticks_lefts =", ticks_lefts)
    print("[events #1]")
    for e in out1.events:
        print(" ", e)
    print("[events #2]")
    for e in out2.events:
        print(" ", e)

    assert ticks_lefts == [1, 3]

    captured = capsys.readouterr().out
    _write_test_result(__file__, request.node.name, inspect.getdoc(test_phase21_22_modifier_stacks_as_distinct_instances_not_duration_extend) or "", captured)


def test_phase22_apply_hp_delta_is_immediate_and_clamped(capsys, request):
    """
    TITLE: APPLY_HP_DELTA가 즉시 반영되고 0 아래로 내려가면 clamp되는지 검증
        EXPECTED:
          - hp: 5 -> 0
          - is_down: True
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0), max_hp=50)
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    bs.combatants[tgt].hp = 5
    assert bs.combatants[tgt].hp == 5
    assert bs.combatants[tgt].is_down is False

    skill = Skill(
        skill_id="test_hp_delta_1",
        name="test_hp_delta_1",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_HP_DELTA",
                target=tgt,
                hp_delta=-999,
            )
        ],
    )
    out = eng.apply_skill(bs, skill)

    print("\n[Phase22] HP_DELTA events:")
    for e in out.events:
        print(" ", e)

    assert bs.combatants[tgt].hp == 0
    assert bs.combatants[tgt].is_down is True

    captured = capsys.readouterr().out
    _write_test_result(__file__, request.node.name, inspect.getdoc(test_phase22_apply_hp_delta_is_immediate_and_clamped) or "", captured)


def test_phase22_apply_hp_delta_is_immediate_and_clamped_2(capsys, request):
    """
    TITLE: APPLY_HP_DELTA가 즉시 반영되고 최대 체력 이상 올라가면 clamp되는지 검증
        EXPECTED:
          - hp: 5 -> 50
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0), max_hp=50)
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    bs.combatants[tgt].hp = 5
    assert bs.combatants[tgt].hp == 5
    assert bs.combatants[tgt].is_down is False

    skill = Skill(
        skill_id="test_hp_delta_2",
        name="test_hp_delta_2",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[
            Step(
                kind="APPLY_HP_DELTA",
                target=tgt,
                hp_delta=999,
            )
        ],
    )
    out = eng.apply_skill(bs, skill)

    print("\n[Phase22] HP_DELTA events:")
    for e in out.events:
        print(" ", e)

    assert bs.combatants[tgt].hp == 50
    captured = capsys.readouterr().out
    _write_test_result(__file__, request.node.name, inspect.getdoc(test_phase22_apply_hp_delta_is_immediate_and_clamped_2) or "", captured)
