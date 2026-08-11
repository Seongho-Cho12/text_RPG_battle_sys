from __future__ import annotations

from dataclasses import replace
from typing import Optional
import uuid

from battle_system.core.commands import Step
from battle_system.core.models import BattleState, ModifierInstance
from battle_system.core.types import CombatantID
from battle_system.formation.movement import disengage, engage
from battle_system.formation.reactions import reaction_attack_candidates
from battle_system.rules.checks import roll_status_success
from battle_system.rules.indices.crit import CritStat
from battle_system.rules.indices.facade import IndexModifiers
from battle_system.rules.indices.status import compute_status_resist_index
from battle_system.timebase.durations import turns_to_ticks_for_battle


OUTCOME_RANK = {"EVADE": 0, "WEAK": 1, "STRONG": 2, "CRITICAL": 3}


def apply_step(engine, bs: BattleState, *, actor: CombatantID, s: Step, crit_stat: CritStat) -> tuple[int, list[str]]:
    if s.target == "SELF":
        s = replace(s, target=actor)

    events: list[str] = []
    prev_gid = bs.combatants[actor].group_id
    result = 1
    anchor = resolve_anchor(s) if _needs_anchor(s) else None

    if s.kind == "MOVE_ENGAGE":
        if s.target is None:
            raise ValueError("MOVE_ENGAGE requires target")
        engage(bs, actor=actor, target=s.target)
        events.append(f"STEP: MOVE_ENGAGE {actor}->{s.target}")
        events.extend(_reaction_events(engine, bs, actor, prev_gid, s))

    elif s.kind == "MOVE_DISENGAGE":
        new_gid = disengage(bs, actor=actor)
        events.append(f"STEP: MOVE_DISENGAGE {actor} -> new_group={new_gid}")
        events.extend(_reaction_events(engine, bs, actor, prev_gid, s))

    elif s.kind == "ATTACK":
        targets, early = _targets_or_event(bs, actor, anchor, s, op="ATTACK")
        if early:
            return 0, [early]
        result = _attack_targets(engine, bs, actor, targets, s.attack_modifiers, crit_stat, "ATTACK", events)

    elif s.kind == "APPLY_EFFECT":
        _require(s.effect_id and s.effect_duration is not None, "APPLY_EFFECT requires effect_id/effect_duration(turns)")
        _require(s.status_inflict is not None, "APPLY_EFFECT requires status_inflict")
        targets, early = _targets_or_event(bs, actor, anchor, s, op="APPLY_EFFECT", detail=f"effect={s.effect_id}")
        if early:
            return 0, [early]
        result = _apply_effect(engine, bs, actor, targets, s, events)

    elif s.kind == "REMOVE_EFFECT":
        _require(bool(s.effect_id), "REMOVE_EFFECT requires effect_id")
        targets, early = _targets_or_event(bs, actor, anchor, s, op="REMOVE_EFFECT", detail=f"effect={s.effect_id}")
        if early:
            return 0, [early]
        result = _remove_effect(engine, bs, actor, targets, s.effect_id, events)

    elif s.kind == "APPLY_MODIFIER":
        _require(
            s.modifier_key is not None and s.modifier_delta is not None and s.modifier_duration is not None,
            "APPLY_MODIFIER requires modifier_key/modifier_delta/modifier_duration",
        )
        targets, early = _targets_or_event(bs, actor, anchor, s, op="APPLY_MODIFIER", detail=f"key={s.modifier_key}")
        if early:
            return 0, [early]
        result = _apply_modifier(bs, targets, s, events)

    elif s.kind == "APPLY_HP_DELTA":
        _require(s.hp_delta is not None, "APPLY_HP_DELTA requires hp_delta")
        targets, early = _targets_or_event(bs, actor, anchor, s, op="APPLY_HP_DELTA", detail=f"delta={int(s.hp_delta)}")
        if early:
            return 0, [early]
        for tgt in targets:
            before = bs.combatants[tgt].hp
            bs.combatants[tgt].hp = before + int(s.hp_delta)
            events.append(f"HP_DELTA: {tgt} {before}->{bs.combatants[tgt].hp} (delta={int(s.hp_delta)})")

    elif s.kind == "TACTICAL_STEALTH":
        result = _tactical_stealth(engine, bs, actor, s, events)

    elif s.kind == "TACTICAL_ESCAPE":
        result = _tactical_escape(engine, bs, actor, events)

    elif s.kind == "TACTICAL_DETECT_STEALTH":
        result = _detect_stealth(engine, bs, actor, s, events)

    elif s.kind == "TACTICAL_THROW":
        anchor = resolve_anchor(s)
        result = _tactical_throw(engine, bs, actor, anchor, s, events)

    else:
        raise ValueError(f"Unknown Step.kind: {s.kind}")

    return result, events


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _needs_anchor(s: Step) -> bool:
    return s.kind in {
        "MOVE_ENGAGE",
        "ATTACK",
        "APPLY_EFFECT",
        "REMOVE_EFFECT",
        "APPLY_MODIFIER",
        "APPLY_HP_DELTA",
    }


def resolve_anchor(s: Step) -> Optional[CombatantID]:
    if s.area == "ALL":
        return s.target
    if s.target is None:
        raise ValueError("Step.target is required unless area == 'ALL'")
    return s.target


def check_range(bs: BattleState, actor: CombatantID, anchor: Optional[CombatantID], s: Step) -> bool:
    if s.range == "ANY":
        return True
    if anchor is None:
        return False
    a_gid = bs.combatants[actor].group_id
    t_gid = bs.combatants[anchor].group_id
    if s.range == "MELEE":
        return a_gid == t_gid
    if s.range == "RANGED":
        return a_gid != t_gid
    return True


def resolve_targets(bs: BattleState, anchor: Optional[CombatantID], s: Step) -> list[CombatantID]:
    if s.area == "ALL":
        return list(bs.combatants.keys())

    assert anchor is not None
    if s.area == "SINGLE":
        return [anchor]
    if s.area == "GROUP":
        anchor_state = bs.combatants[anchor]
        return [
            cid
            for cid in bs.groups.get(anchor_state.group_id, [])
            if bs.combatants[cid].team == anchor_state.team
        ]
    raise ValueError(f"Unknown area: {s.area}")


def _targets_or_event(
    bs: BattleState,
    actor: CombatantID,
    anchor: Optional[CombatantID],
    s: Step,
    *,
    op: str,
    detail: str = "",
) -> tuple[list[CombatantID], Optional[str]]:
    _require(s.target is not None or s.area == "ALL", f"{op} requires target unless area == 'ALL'")
    prefix = f"{op} actor={actor} anchor={anchor}"
    if detail:
        prefix = f"{prefix} {detail}"
    if not check_range(bs, actor, anchor, s):
        return [], f"OUT_OF_RANGE: {prefix} range={s.range} area={s.area}"
    targets = resolve_targets(bs, anchor, s)
    if not targets:
        return [], f"NO_TARGETS: {prefix} range={s.range} area={s.area}"
    return targets, None


def _reaction_events(engine, bs: BattleState, actor: CombatantID, prev_gid, s: Step) -> list[str]:
    stealth = bs.combatants[actor].effects.get("STEALTH", 0) > 0
    cands = reaction_attack_candidates(
        bs,
        mover=actor,
        prev_group_id=prev_gid,
        reaction_immune=s.reaction_immune or stealth,
    )
    return engine._run_reactions(bs, mover=actor, cands=cands, reaction_hit_penalty=s.reaction_hit_penalty)


def _attack_targets(
    engine,
    bs: BattleState,
    actor: CombatantID,
    targets: list[CombatantID],
    modifiers: IndexModifiers,
    crit_stat: CritStat,
    label: str,
    events: list[str],
) -> int:
    best = 0
    for tgt in targets:
        r = engine._basic_attack(bs, attacker=actor, defender=tgt, modifiers=modifiers, crit_stat=crit_stat)
        if bs.combatants[actor].effects.get("STEALTH", 0) > 0 and r.get("hit", False):
            del bs.combatants[actor].effects["STEALTH"]
            events.append(f"STEALTH_BROKEN: {actor} (hit_success)")
        events.append(f"STEP: {label} {actor}->{tgt} outcome={r['outcome']} dmg={r['damage']}")
        best = max(best, int(OUTCOME_RANK.get(r["outcome"], 0)))
    return best


def _apply_effect(engine, bs: BattleState, actor: CombatantID, targets: list[CombatantID], s: Step, events: list[str]) -> int:
    eff = str(s.effect_id)
    dur_ticks = turns_to_ticks_for_battle(bs, int(s.effect_duration))
    success_any = 0

    for tgt in targets:
        base_inflict = int(s.status_inflict)
        base_resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)
        inflict_bonus = engine._sum_status_tag_mod(bs, actor, key="STATUS_INFLICT", status_id=eff)
        resist_bonus = engine._sum_status_tag_mod(bs, tgt, key="STATUS_RESIST", status_id=eff)

        if not base_resist.resistible:
            prev = bs.combatants[tgt].effects.get(eff, 0)
            bs.combatants[tgt].effects[eff] = prev + dur_ticks
            events.append(
                f"STATUS_CHECK: {actor}->{tgt} effect={eff} "
                f"inflict={base_inflict}+{inflict_bonus} "
                f"resist=NA resistible=False roll=NA success=True"
            )
            events.append(
                f"EFFECT_APPLIED: {tgt} +{eff}(turns={s.effect_duration}, ticks=+{dur_ticks}, total_ticks={prev + dur_ticks})"
            )
            engine._apply_instant_death(bs, actor=actor, tgt=tgt, events=events)
            success_any = 1
            continue

        sr = roll_status_success(inflict=base_inflict + inflict_bonus, resist=int(base_resist.value) + resist_bonus)
        events.append(
            f"STATUS_CHECK: {actor}->{tgt} effect={eff} "
            f"inflict={base_inflict}+{inflict_bonus} "
            f"resist={base_resist.value}+{resist_bonus} resistible=True roll={sr.roll} success={sr.success}"
        )
        if sr.success:
            prev = bs.combatants[tgt].effects.get(eff, 0)
            bs.combatants[tgt].effects[eff] = prev + dur_ticks
            events.append(
                f"EFFECT_APPLIED: {tgt} +{eff}(turns={s.effect_duration}, ticks=+{dur_ticks}, total_ticks={prev + dur_ticks})"
            )
            success_any = 1
        else:
            events.append(f"EFFECT_RESISTED: {tgt} resisted {eff}")

    return 1 if success_any else 0


def _remove_effect(engine, bs: BattleState, actor: CombatantID, targets: list[CombatantID], eff: str, events: list[str]) -> int:
    success_any = 0
    dispel_inflict = engine._dispel_inflict()

    for tgt in targets:
        if eff not in bs.combatants[tgt].effects:
            events.append(f"EFFECT_REMOVE_NOOP: {tgt} has_no {eff}")
            continue

        resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)
        resist_bonus = engine._sum_status_tag_mod(bs, tgt, key="STATUS_RESIST", status_id=eff)

        if not resist.resistible:
            events.append(
                f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                f"inflict={dispel_inflict} resist=NA resistible=False roll=NA success=True"
            )
            events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
            continue

        sr = roll_status_success(inflict=int(dispel_inflict), resist=int(resist.value) + resist_bonus)
        events.append(
            f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
            f"inflict={dispel_inflict} resist={resist.value}+{resist_bonus} resistible=True roll={sr.roll} success={sr.success}"
        )
        if sr.success:
            events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
        else:
            del bs.combatants[tgt].effects[eff]
            events.append(f"DISPEL_SUCCESS: {tgt} -{eff}")
            success_any = 1

    return 1 if success_any else 0


def _apply_modifier(bs: BattleState, targets: list[CombatantID], s: Step, events: list[str]) -> int:
    dur_ticks = turns_to_ticks_for_battle(bs, int(s.modifier_duration))
    for tgt in targets:
        tag = None
        if s.modifier_key in ("STATUS_RESIST", "STATUS_INFLICT"):
            if not s.modifier_status_tag:
                raise ValueError("APPLY_MODIFIER: STATUS_RESIST/STATUS_INFLICT requires modifier_status_tag ('ALL' or StatusID)")
            tag = str(s.modifier_status_tag)
        mi = ModifierInstance(
            mid=uuid.uuid4().hex,
            key=s.modifier_key,
            delta=int(s.modifier_delta),
            ticks_left=dur_ticks,
            status_tag=tag,
        )
        bs.combatants[tgt].modifiers.append(mi)
        events.append(
            f"MOD_APPLIED: {tgt} mid={mi.mid} key={s.modifier_key} delta={mi.delta} "
            f"turns={s.modifier_duration} ticks={dur_ticks} tag={tag}"
        )
    return 1 if targets else 0


def _tactical_stealth(engine, bs: BattleState, actor: CombatantID, s: Step, events: list[str]) -> int:
    _require(s.effect_duration is not None, "TACTICAL_STEALTH requires effect_duration(turns)")
    inflict = engine._effective_stat(bs, actor, "AGI") + engine._sum_mod(bs, actor, key="STEALTH_INFLICT")
    resist = engine._enemy_wis_avg_with_resist(bs, actor, resist_key="STEALTH_RESIST")
    sr = roll_status_success(inflict=inflict, resist=resist)
    events.append(f"STEALTH_CHECK: actor={actor} inflict={inflict} resist={resist} roll={sr.roll} success={sr.success}")
    if not sr.success:
        return 0

    dur_ticks = turns_to_ticks_for_battle(bs, int(s.effect_duration))
    prev = bs.combatants[actor].effects.get("STEALTH", 0)
    bs.combatants[actor].effects["STEALTH"] = prev + dur_ticks
    events.append(f"STEALTH_APPLIED: {actor} turns={s.effect_duration} ticks=+{dur_ticks} total_ticks={prev + dur_ticks}")
    return 1


def _tactical_escape(engine, bs: BattleState, actor: CombatantID, events: list[str]) -> int:
    inflict = (
        engine._effective_stat(bs, actor, "AGI")
        + engine._effective_stat(bs, actor, "WIS")
        + engine._sum_mod(bs, actor, key="ESCAPE_INFLICT")
    )
    if bs.combatants[actor].effects.get("STEALTH", 0) > 0:
        inflict += 10
    resist = engine._enemy_wis_avg_with_resist(bs, actor, resist_key="ESCAPE_RESIST")
    sr = roll_status_success(inflict=inflict, resist=resist)
    events.append(f"ESCAPE_CHECK: actor={actor} inflict={inflict} resist={resist} roll={sr.roll} success={sr.success}")
    if not sr.success:
        return 0

    bs.ended = True
    bs.end_reason = "ESCAPE"
    events.append("BATTLE_ENDED: ESCAPE")
    return 1


def _detect_stealth(engine, bs: BattleState, actor: CombatantID, s: Step, events: list[str]) -> int:
    if s.target is None:
        events.append("DETECT_STEALTH: missing target")
        return 0

    tgt = s.target
    if tgt not in bs.combatants or bs.combatants[tgt].is_down:
        events.append(f"DETECT_STEALTH: invalid target {tgt}")
        return 0
    if bs.combatants[tgt].effects.get("STEALTH", 0) <= 0:
        events.append(f"DETECT_STEALTH: target {tgt} not stealth")
        return 0

    same_group = bs.combatants[actor].group_id == bs.combatants[tgt].group_id
    tgt_agi = engine._effective_stat(bs, tgt, "AGI")
    inflict = ((tgt_agi // 2) if same_group else tgt_agi) + engine._sum_mod(bs, tgt, key="STEALTH_INFLICT")
    resist = int(engine._effective_stat(bs, actor, "WIS") * 1.5) + engine._sum_mod(bs, actor, key="STEALTH_RESIST")
    sr = roll_status_success(inflict=inflict, resist=resist)
    events.append(
        f"DETECT_STEALTH_CHECK: actor={actor} target={tgt} same_group={same_group} "
        f"inflict={inflict} resist={resist} roll={sr.roll} stealth_success={sr.success}"
    )

    if sr.success:
        events.append(f"DETECT_STEALTH_FAIL: target={tgt} remains_stealth")
        return 0

    del bs.combatants[tgt].effects["STEALTH"]
    events.append(f"DETECT_STEALTH_SUCCESS: target={tgt} stealth_removed")
    return 1


def _tactical_throw(engine, bs: BattleState, actor: CombatantID, anchor: Optional[CombatantID], s: Step, events: list[str]) -> int:
    if not s.throw_item_id:
        events.append("THROW: missing throw_item_id")
        return 0

    item_id = s.throw_item_id
    if item_id not in bs.items:
        events.append(f"THROW: unknown item_id {item_id}")
        return 0

    targets, early = _targets_or_event(bs, actor, anchor, s, op="ATTACK")
    if early:
        events.append(early)
        return 0

    if not engine._inv_snapshot_consume_one(bs, actor, item_id):
        events.append(f"THROW: item not available {item_id}")
        return 0

    engine._inv_delta_add(bs, actor, item_id, -1)
    weight = bs.items[item_id].weight
    throw_mods = engine._throw_instant_mods(bs, actor, weight)
    step_mods = s.attack_modifiers or IndexModifiers()
    combined = IndexModifiers(
        hit=throw_mods.hit + step_mods.hit,
        evade=throw_mods.evade + step_mods.evade,
        weak=throw_mods.weak + step_mods.weak,
        strong=throw_mods.strong + step_mods.strong,
        critical=throw_mods.critical + step_mods.critical,
    )

    events.append(f"THROW_CONSUME: actor={actor} item={item_id} weight={weight} mods={combined}")
    return _attack_targets(engine, bs, actor, targets, combined, "STR", "THROW_ATTACK", events)
