from __future__ import annotations

import re
from typing import Tuple, List

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Skill, Step


# -------------------------
# helpers: battle creation
# -------------------------

def _mk_char(cid: str, *, level: int = 10, max_hp: int = 50) -> CharacterDef:
    return CharacterDef(
        cid=CombatantID(cid),
        name=cid,
        level=level,
        stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10),
        max_hp=max_hp,
    )


def _mk_engine_1v1() -> Tuple[BattleEngine, object, CombatantID, CombatantID]:
    eng = BattleEngine()
    a = _mk_char("A")
    e = _mk_char("E")
    bs = eng.create_battle([a], [e])
    return eng, bs, CombatantID("A"), CombatantID("E")


def _events(outcome) -> List[str]:
    return list(getattr(outcome, "events", []))


def _find_line(events: List[str], prefix: str) -> str:
    for e in events:
        if e.startswith(prefix):
            return e
    raise AssertionError(f"Expected event starting with '{prefix}' not found.\nEvents:\n" + "\n".join(events))


def _parse_inflict_bonus(line: str) -> int:
    m = re.search(r"inflict=(\d+)\+(-?\d+)", line)
    assert m, f"inflict pattern not found in: {line}"
    return int(m.group(2))


def _parse_resist_bonus(line: str) -> int:
    m = re.search(r"resist=(\d+)\+(-?\d+)", line)
    assert m, f"resist pattern not found in: {line}"
    return int(m.group(2))


def _advance_turn(eng: BattleEngine, bs, k: int = 1) -> None:
    """
    MAIN/SUB 사용 제한을 피하기 위해 turn을 넘긴다.
    - 1v1이라 한 번 end_turn하면 actor가 바뀐다.
    - 다시 A 차례로 오려면 2번 end_turn이 필요.
    """
    for _ in range(k):
        eng.end_turn(bs)


def _advance_to_actor(eng: BattleEngine, bs, actor: CombatantID) -> None:
    """
    원하는 actor 턴이 될 때까지 end_turn 반복.
    1v1이면 최대 2번이면 충분.
    """
    for _ in range(3):
        eng.end_turn(bs)
        if bs.current_actor_id() == actor:
            return
    raise AssertionError("failed to advance to desired actor")


# -------------------------
# helpers: skills
# -------------------------

def _skill_apply_effect(actor: CombatantID, target: CombatantID, *, action_type: str, eff: str) -> Skill:
    return Skill(
        skill_id=f"P28_APPLY_{eff}_{action_type}",
        name="apply_effect",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=target,
                effect_id=eff,          # ✅ 대문자 StatusID
                effect_duration=1,
                status_inflict=10,      # base inflict
                range="ANY",
                area="SINGLE",
            )
        ],
    )


def _skill_apply_status_modifier(
    actor: CombatantID,
    target: CombatantID,
    *,
    action_type: str,
    key: str,
    delta: int,
    tag: str | None,
) -> Skill:
    return Skill(
        skill_id=f"P28_MOD_{key}_{tag}_{action_type}",
        name="apply_mod",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_MODIFIER",
                target=target,
                modifier_key=key,
                modifier_delta=delta,
                modifier_duration=5,
                modifier_status_tag=tag,  # ✅ "ALL" or StatusID
                range="ANY",
                area="SINGLE",
            )
        ],
    )


def _skill_remove_effect(actor: CombatantID, target: CombatantID, *, action_type: str, eff: str) -> Skill:
    return Skill(
        skill_id=f"P28_REMOVE_{eff}_{action_type}",
        name="remove_effect",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="REMOVE_EFFECT",
                target=target,
                effect_id=eff,  # ✅ 대문자
                range="ANY",
                area="SINGLE",
            )
        ],
    )


# -------------------------
# Phase 28 tests
# -------------------------

def test_phase28_specific_status_resist_applies_only_to_matching_effect():
    """
    [Phase 28] STATUS_RESIST(tag=BLEEDING) 보정은 BLEEDING 굴림에만 적용,
              POISONED 굴림에는 적용되면 안 된다.
    """
    eng, bs, A, E = _mk_engine_1v1()

    _advance_to_actor(eng, bs, A)
    eng.apply_skill(bs, _skill_apply_status_modifier(A, E, action_type="MAIN", key="STATUS_RESIST", delta=50, tag="BLEEDING"))

    # 턴 넘겨서 다시 A 차례로
    _advance_to_actor(eng, bs, A)
    out_p = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="POISONED"))
    line_p = _find_line(_events(out_p), "STATUS_CHECK:")
    assert _parse_resist_bonus(line_p) == 0

    _advance_to_actor(eng, bs, A)
    out_b = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="BLEEDING"))
    line_b = _find_line(_events(out_b), "STATUS_CHECK:")
    assert _parse_resist_bonus(line_b) == 50


def test_phase28_all_status_resist_applies_to_every_effect():
    """
    [Phase 28] STATUS_RESIST(tag=ALL) 보정은 모든 effect 굴림에 적용되어야 한다.
    """
    eng, bs, A, E = _mk_engine_1v1()

    _advance_to_actor(eng, bs, A)
    eng.apply_skill(bs, _skill_apply_status_modifier(A, E, action_type="MAIN", key="STATUS_RESIST", delta=7, tag="ALL"))

    _advance_to_actor(eng, bs, A)
    out_p = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="POISONED"))
    line_p = _find_line(_events(out_p), "STATUS_CHECK:")
    assert _parse_resist_bonus(line_p) == 7

    _advance_to_actor(eng, bs, A)
    out_b = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="BLEEDING"))
    line_b = _find_line(_events(out_b), "STATUS_CHECK:")
    assert _parse_resist_bonus(line_b) == 7


def test_phase28_all_and_specific_resist_stack():
    """
    [Phase 28] STATUS_RESIST(tag=ALL) + STATUS_RESIST(tag=BLEEDING)가 누적되어야 한다.
    """
    eng, bs, A, E = _mk_engine_1v1()

    _advance_to_actor(eng, bs, A)
    eng.apply_skill(bs, _skill_apply_status_modifier(A, E, action_type="MAIN", key="STATUS_RESIST", delta=10, tag="ALL"))

    _advance_to_actor(eng, bs, A)
    eng.apply_skill(bs, _skill_apply_status_modifier(A, E, action_type="MAIN", key="STATUS_RESIST", delta=20, tag="BLEEDING"))

    _advance_to_actor(eng, bs, A)
    out_b = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="BLEEDING"))
    line_b = _find_line(_events(out_b), "STATUS_CHECK:")
    assert _parse_resist_bonus(line_b) == 30

    _advance_to_actor(eng, bs, A)
    out_p = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="POISONED"))
    line_p = _find_line(_events(out_p), "STATUS_CHECK:")
    assert _parse_resist_bonus(line_p) == 10


def test_phase28_specific_status_inflict_applies_only_to_matching_effect():
    """
    [Phase 28] STATUS_INFLICT(tag=POISONED) 보정은 POISONED 부여 굴림에만 적용,
              BLEEDING 굴림에는 적용되면 안 된다.
    """
    eng, bs, A, E = _mk_engine_1v1()

    _advance_to_actor(eng, bs, A)
    eng.apply_skill(bs, _skill_apply_status_modifier(A, A, action_type="MAIN", key="STATUS_INFLICT", delta=15, tag="POISONED"))

    _advance_to_actor(eng, bs, A)
    out_p = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="POISONED"))
    line_p = _find_line(_events(out_p), "STATUS_CHECK:")
    assert _parse_inflict_bonus(line_p) == 15

    _advance_to_actor(eng, bs, A)
    out_b = eng.apply_skill(bs, _skill_apply_effect(A, E, action_type="MAIN", eff="BLEEDING"))
    line_b = _find_line(_events(out_b), "STATUS_CHECK:")
    assert _parse_inflict_bonus(line_b) == 0


def test_phase28_apply_modifier_requires_tag_for_status_keys():
    """
    [Phase 28] APPLY_MODIFIER에서 STATUS_RESIST/STATUS_INFLICT이면 tag가 없으면 ValueError.
    """
    eng, bs, A, E = _mk_engine_1v1()

    _advance_to_actor(eng, bs, A)
    bad = _skill_apply_status_modifier(A, E, action_type="MAIN", key="STATUS_RESIST", delta=10, tag=None)
    with pytest.raises(ValueError):
        eng.apply_skill(bs, bad)


def test_phase28_remove_effect_uses_matching_status_resist_bonus_in_log():
    """
    [Phase 28] REMOVE_EFFECT(디스펠 체크)에서도 matching STATUS_RESIST(tag=effect or ALL)만 합산되어야 한다.
    """
    eng, bs, A, E = _mk_engine_1v1()

    # effect exists so REMOVE_EFFECT won't noop
    bs.combatants[E].effects["BLEEDING"] = 5

    _advance_to_actor(eng, bs, A)
    eng.apply_skill(bs, _skill_apply_status_modifier(A, E, action_type="MAIN", key="STATUS_RESIST", delta=12, tag="BLEEDING"))

    _advance_to_actor(eng, bs, A)
    out = eng.apply_skill(bs, _skill_remove_effect(A, E, action_type="MAIN", eff="BLEEDING"))
    line = _find_line(_events(out), "DISPEL_CHECK:")
    assert _parse_resist_bonus(line) == 12
