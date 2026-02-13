# battle_system/app/skill_ui.py
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple, Literal

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState, UseSkillDef, ItemDef
from battle_system.core.commands import Skill, Step, ActionType


AvailabilityReason = Literal[
    "OK",
    "NOT_MY_TURN",
    "ACTOR_DOWN",
    "NO_ACTION_SLOT",
    "ON_COOLDOWN",
    "BLOCKED_BY_EFFECT",
    "MISSING_INPUT",
    "NO_VALID_TARGET",
    "NO_THROWABLE_ITEM",
    "OUT_OF_RANGE",
    "NO_ITEM_STOCK",       # Phase 38: consume_item_id 아이템 수량 부족
]


@dataclass(frozen=True)
class SkillInputSpec:
    target_required: bool
    item_required: bool
    target_candidates: List[CombatantID]
    item_candidates: List[str]


@dataclass(frozen=True)
class SkillAvailability:
    usable: bool
    reason: AvailabilityReason
    # 엔진과 동일한 메시지를 넣어두면 UI/로그에서 그대로 보여줄 수 있음
    engine_message: str
    spec: SkillInputSpec


def list_usable_skills(
    bs: BattleState,
    *,
    actor: CombatantID,
    skills: List[Skill],
    action_type: ActionType,
) -> List[Tuple[Skill, SkillAvailability]]:
    out: List[Tuple[Skill, SkillAvailability]] = []
    for sk in skills:
        if sk.actor != actor:
            continue
        if sk.action_type != action_type:
            continue
        av = get_skill_availability(bs, sk)
        if av.usable:
            out.append((sk, av))
    return out


def get_skill_availability(bs: BattleState, sk: Skill) -> SkillAvailability:
    """
    엔진 apply_skill이 막는 조건을 '상태 변화 없이' UI에서 선판정.
    - ✅ 쿨다운 남아있으면 무조건 사용 불가 (ON_COOLDOWN)
    - ✅ 상태이상 사용 제한은 엔진 로직과 1:1로 복제
    """
    actor = bs.current_actor_id()

    if sk.actor != actor:
        return SkillAvailability(False, "NOT_MY_TURN", "", _empty_spec())

    st = bs.combatants[actor]
    if st.is_down:
        return SkillAvailability(False, "ACTOR_DOWN", "", _empty_spec())

    # 행동 슬롯 체크
    if sk.action_type == "MAIN":
        if not st.can_main:
            return SkillAvailability(False, "NO_ACTION_SLOT", "", _empty_spec())
    else:
        if not st.can_sub:
            return SkillAvailability(False, "NO_ACTION_SLOT", "", _empty_spec())

    # ✅ 쿨다운 체크 (엔진과 동일하게 cooldowns dict 기반)
    if sk.cooldown_turns > 0:
        left = st.cooldowns.get(sk.skill_id, 0)
        if left > 0:
            return SkillAvailability(False, "ON_COOLDOWN", "", _empty_spec())

    # ✅ 상태이상에 의한 사용 제한 (엔진 로직 그대로 복제)
    ok, msg = can_use_skill_due_to_effects_like_engine(bs, actor, sk)
    if not ok:
        return SkillAvailability(False, "BLOCKED_BY_EFFECT", msg, _empty_spec())

    # 입력 요구 계산 + 후보 계산
    spec = compute_input_spec(bs, actor, sk)

    # Phase 38: consume_item_id 아이템 수량 체크
    if sk.consume_item_id is not None:
        snap = bs.inventory_snapshot.get(actor, {})
        if snap.get(sk.consume_item_id, 0) <= 0:
            return SkillAvailability(False, "NO_ITEM_STOCK", "", spec)

    if spec.item_required and not spec.item_candidates:
        return SkillAvailability(False, "NO_THROWABLE_ITEM", "", spec)

    if spec.target_required and not spec.target_candidates:
        return SkillAvailability(False, "NO_VALID_TARGET", "", spec)

    # 엔진의 _check_range가 anchor None이면 False가 되는 케이스(ALL + target None + MELEE/RANGED) 방지
    if _has_invalid_range_without_anchor(sk):
        return SkillAvailability(False, "OUT_OF_RANGE", "", spec)

    return SkillAvailability(True, "OK", "", spec)


def compute_input_spec(bs: BattleState, actor: CombatantID, sk: Skill) -> SkillInputSpec:
    target_required = False
    item_required = False

    for s in (sk.steps or []):
        if s.kind == "TACTICAL_THROW":
            item_required = True
        if _step_needs_target(s) and s.target is None:
            target_required = True

    target_candidates: List[CombatantID] = []
    if target_required:
        target_candidates = _compute_target_candidates_union(bs, actor, sk)

    item_candidates: List[str] = []
    if item_required:
        snap = bs.inventory_snapshot.get(actor, {})
        item_candidates = [item_id for item_id, cnt in snap.items() if int(cnt) > 0]

    return SkillInputSpec(
        target_required=target_required,
        item_required=item_required,
        target_candidates=target_candidates,
        item_candidates=item_candidates,
    )


def instantiate_skill_with_inputs(
    sk: Skill,
    *,
    target: Optional[CombatantID] = None,
    throw_item_id: Optional[str] = None,
) -> Skill:
    """
    UI 입력을 반영해 실행용 Skill 인스턴스 생성.
    - Step.target None인 곳에 target 채움
    - TACTICAL_THROW에 throw_item_id 채움
    """
    new_steps: List[Step] = []
    for s in (sk.steps or []):
        ns = s

        if _step_needs_target(s) and s.target is None:
            if target is None:
                raise ValueError("target is required but not provided")
            ns = replace(ns, target=target)

        if s.kind == "TACTICAL_THROW":
            if throw_item_id is None:
                raise ValueError("throw_item_id is required but not provided")
            ns = replace(ns, throw_item_id=throw_item_id)

        new_steps.append(ns)

    return replace(sk, steps=new_steps)


# -----------------------------------------------------------------------------
# 엔진 로직 1:1 복제 (Phase 26)
# -----------------------------------------------------------------------------

def has_effect(bs: BattleState, cid: CombatantID, eff: str) -> bool:
    # engine.py: return bs.combatants[cid].effects.get(eff, 0) > 0
    return bs.combatants[cid].effects.get(eff, 0) > 0


def skill_has_move(skill: Skill) -> bool:
    # engine.py:
    # - kind가 문자열이면 startswith("MOVE")도 True
    # - s.kind == "MOVE"도 True
    for s in (skill.steps or []):
        if isinstance(s.kind, str) and s.kind.startswith("MOVE"):
            return True
        if s.kind == "MOVE":
            return True
    return False


def skill_has_apply_effect(skill: Skill) -> bool:
    # engine.py: s.kind == "APPLY_EFFECT" 포함 여부
    for s in (skill.steps or []):
        if s.kind == "APPLY_EFFECT":
            return True
    return False


def is_magic_skill(skill: Skill) -> bool:
    # engine.py: crit_stat in ("INT","WIS")
    crit_stat = getattr(skill, "crit_stat", "STR")
    return crit_stat in ("INT", "WIS")


def can_use_skill_due_to_effects_like_engine(
    bs: BattleState,
    actor: CombatantID,
    skill: Skill,
) -> tuple[bool, str]:
    """
    engine.py의 _can_use_skill_due_to_effects()를 문장/순서/조건 그대로 복제.
    반환 메시지도 엔진과 동일.
    """
    # 행동 불가
    if has_effect(bs, actor, "STUN"):
        return False, "STUN: action blocked"
    if has_effect(bs, actor, "PARALYSIS"):
        return False, "PARALYSIS: action blocked"
    if has_effect(bs, actor, "FROZEN"):
        return False, "FROZEN: action blocked"

    # BIND: 이동 포함 스킬 금지
    if has_effect(bs, actor, "BIND") and skill_has_move(skill):
        return False, "BIND: move skill blocked"

    # OBLIVION: 2스텝 이상 스킬 금지
    if has_effect(bs, actor, "OBLIVION") and len(skill.steps) >= 2:
        return False, "OBLIVION: multi-step skill blocked"

    # CURSE: "마법 공격" or "상태이상 부여 포함" 스킬 금지
    if has_effect(bs, actor, "CURSE"):
        if is_magic_skill(skill) or skill_has_apply_effect(skill):
            return False, "CURSE: magic or apply_effect skill blocked"

    return True, ""


# -----------------------------------------------------------------------------
# 내부 유틸
# -----------------------------------------------------------------------------

def _empty_spec() -> SkillInputSpec:
    return SkillInputSpec(False, False, [], [])


def _step_needs_target(s: Step) -> bool:
    # Step 정의/엔진 처리와 맞춘 최소 규칙
    if s.kind == "MOVE_ENGAGE":
        return True
    if s.kind in ("ATTACK", "APPLY_EFFECT", "REMOVE_EFFECT", "APPLY_MODIFIER", "APPLY_HP_DELTA", "DETECT_STEALTH"):
        return s.area != "ALL"
    return False


def _compute_target_candidates_union(bs: BattleState, actor: CombatantID, sk: Skill) -> List[CombatantID]:
    """
    Phase34+35 후보 계산:
    - DOWN 제외
    - range 기반으로 최소 필터
    - area=GROUP은 anchor만 고르면 엔진이 확장하므로 후보는 SINGLE과 동일 취급
    - Phase 35: target_filter(SELF/ALLY/ENEMY/ANY)로 최종 필터링
    """
    tf = getattr(sk, "target_filter", "ANY")
    a_gid = bs.combatants[actor].group_id

    # SELF/ALLY 필터 시 자기 자신도 후보 풀에 포함해야 한다
    if tf in ("SELF", "ALLY"):
        alive_pool = [cid for cid, st in bs.combatants.items() if not st.is_down]
    else:
        alive_pool = [cid for cid, st in bs.combatants.items() if (not st.is_down) and cid != actor]

    cands: set[CombatantID] = set()

    for s in (sk.steps or []):
        if not _step_needs_target(s):
            continue

        if s.kind == "MOVE_ENGAGE":
            cands.update(alive_pool)
            continue

        if s.area == "ALL":
            continue

        if s.range == "ANY":
            cands.update(alive_pool)
        elif s.range == "MELEE":
            cands.update([cid for cid in alive_pool if bs.combatants[cid].group_id == a_gid])
        elif s.range == "RANGED":
            cands.update([cid for cid in alive_pool if bs.combatants[cid].group_id != a_gid])

    # Phase 35: target_filter 적용
    filtered = _filter_targets_by_skill(bs, actor, sk, sorted(cands, key=lambda x: str(x)))
    return filtered


def _has_invalid_range_without_anchor(sk: Skill) -> bool:
    """
    엔진 _check_range는 anchor(target) None이면 MELEE/RANGED 판단이 불가능해서 False가 될 수 있음.
    - area=ALL + target=None + range=MELEE/RANGED 조합이 있으면 UI에서 사용 불가 처리.
    """
    for s in (sk.steps or []):
        if s.kind not in ("ATTACK", "APPLY_EFFECT", "REMOVE_EFFECT", "APPLY_MODIFIER", "APPLY_HP_DELTA"):
            continue
        if s.area == "ALL" and s.target is None and s.range in ("MELEE", "RANGED"):
            return True
    return False

def _filter_targets_by_skill(bs: BattleState, actor: CombatantID, sk: Skill, targets: List[CombatantID]) -> List[CombatantID]:
    """
    Phase 35: Skill.target_filter에 따라 후보 대상을 필터링.
    - ANY:   제한 없음(그대로 반환)
    - SELF:  자기 자신만
    - ALLY:  actor와 같은 team(actor 자신 포함)
    - ENEMY: actor와 다른 team

    진영 판정은 CombatantState.team을 사용한다.
    """
    tf = getattr(sk, "target_filter", "ANY")
    if tf == "ANY":
        return targets
    if tf == "SELF":
        return [t for t in targets if t == actor]

    actor_team = bs.combatants[actor].team
    if tf == "ALLY":
        return [t for t in targets if bs.combatants[t].team == actor_team]
    if tf == "ENEMY":
        return [t for t in targets if bs.combatants[t].team != actor_team]
    return targets


# -----------------------------------------------------------------------------
# Phase 37: 아이템 사용 스킬 인스턴스화
# -----------------------------------------------------------------------------

def build_item_use_skill(
    actor: CombatantID,
    item_id: str,
    use_skill_def: UseSkillDef,
) -> Skill:
    """
    UseSkillDef(아이템 템플릿) + actor + item_id → 실행용 Skill 인스턴스.
    - skill_id = f"USE:{item_id}"
    - consume_item_id = item_id (엔진이 step 실행 직전에 소모)
    - steps/crit_stat/target_filter 등은 템플릿에서 그대로 복사
    """
    return Skill(
        skill_id=f"USE:{item_id}",
        name=use_skill_def.name,
        actor=actor,
        action_type=use_skill_def.action_type,
        cooldown_turns=use_skill_def.cooldown_turns,
        steps=list(use_skill_def.steps),
        crit_stat=use_skill_def.crit_stat,
        target_filter=use_skill_def.target_filter,
        consume_item_id=item_id,
    )


# -----------------------------------------------------------------------------
# Phase 38: Availability 계층 통합
# -----------------------------------------------------------------------------

ItemReason = Literal[
    "OK",
    "NO_STOCK",        # 수량 0
    "NO_USE_SKILL",    # use_skill 없음
    "NO_ACTION_SLOT",  # 행동 슬롯 부족
    "ON_COOLDOWN",     # 쿨다운
    "BLOCKED_BY_EFFECT", # 상태이상
    "NOT_MY_TURN",
    "ACTOR_DOWN",
    "MISSING_INPUT",
    "NO_VALID_TARGET",
    "NO_THROWABLE_ITEM",
    "OUT_OF_RANGE",
]

TargetReason = Literal[
    "OK",
    "DOWN",
    "OUT_OF_RANGE",
    "WRONG_TEAM",
]


@dataclass(frozen=True)
class ItemOption:
    item_id: str
    enabled: bool
    reason: ItemReason
    quantity: int


@dataclass(frozen=True)
class TargetOption:
    target_id: CombatantID
    enabled: bool
    reason: TargetReason


def list_use_items(
    bs: BattleState,
    actor: CombatantID,
    items_registry: Dict[str, ItemDef],
) -> List[ItemOption]:
    """
    Phase 38: 인벤토리의 모든 아이템을 사용가능/불가능으로 분류.
    - use_skill 있고 수량 > 0: enabled
    - use_skill 없음: disabled (NO_USE_SKILL)
    - 수량 0: disabled (NO_STOCK)
    - Phase 43: 실제 스킬 사용 가능 여부(슬롯, 쿨다운 등) 체크
    """
    snap = bs.inventory_snapshot.get(actor, {})
    out: List[ItemOption] = []
    for item_id, qty in snap.items():
        idef = items_registry.get(item_id)
        if qty <= 0:
            out.append(ItemOption(item_id=item_id, enabled=False, reason="NO_STOCK", quantity=qty))
            continue
            
        if idef is None or idef.use_skill is None:
            out.append(ItemOption(item_id=item_id, enabled=False, reason="NO_USE_SKILL", quantity=qty))
            continue

        # Phase 43: 스킬 가용성 체크
        sk = build_item_use_skill(actor, item_id, idef.use_skill)
        av = get_skill_availability(bs, sk)
        
        if av.usable:
            out.append(ItemOption(item_id=item_id, enabled=True, reason="OK", quantity=qty))
        else:
            # AvailabilityReason을 ItemReason으로 매핑 (일부는 그대로 사용)
            reason = av.reason
            # 지원하지 않는 reason은 로그 남기거나 generic하게 처리할 수 있으나, 
            # 현재 ItemReason에 NO_ACTION_SLOT 등을 추가했으므로 그대로 사용.
            # 타입 시스템 만족을 위해 cast 필요할 수 있음.
            out.append(ItemOption(item_id=item_id, enabled=False, reason=reason, quantity=qty))  # type: ignore

    return sorted(out, key=lambda x: x.item_id)


def list_throw_items(
    bs: BattleState,
    actor: CombatantID,
) -> List[ItemOption]:
    """
    Phase 38: 투척 가능 아이템 목록.
    - 수량 > 0: enabled
    - 수량 <= 0: disabled (NO_STOCK)
    """
    snap = bs.inventory_snapshot.get(actor, {})
    out: List[ItemOption] = []
    for item_id, qty in snap.items():
        if qty <= 0:
            out.append(ItemOption(item_id=item_id, enabled=False, reason="NO_STOCK", quantity=qty))
        else:
            out.append(ItemOption(item_id=item_id, enabled=True, reason="OK", quantity=qty))
    return sorted(out, key=lambda x: x.item_id)


def list_target_options(
    bs: BattleState,
    actor: CombatantID,
    sk: Skill,
) -> List[TargetOption]:
    """
    Phase 38: 모든 전투원을 대상으로 가능/불가능 + 사유 표시.
    - 사용 가능 후보: enabled
    - DOWN: disabled (DOWN)
    - target_filter 불일치: disabled (WRONG_TEAM)
    - range 불일치: disabled (OUT_OF_RANGE)
    """
    valid_set = set(_compute_target_candidates_union(bs, actor, sk))
    tf = getattr(sk, "target_filter", "ANY")
    actor_team = bs.combatants[actor].team
    a_gid = bs.combatants[actor].group_id

    out: List[TargetOption] = []
    for cid, st in bs.combatants.items():
        if cid == actor and tf not in ("SELF", "ALLY"):
            continue  # 자기 자신은 SELF/ALLY만 표시

        if st.is_down:
            out.append(TargetOption(target_id=cid, enabled=False, reason="DOWN"))
            continue

        if cid in valid_set:
            out.append(TargetOption(target_id=cid, enabled=True, reason="OK"))
        else:
            # 이유 파악: team 불일치 vs range 불일치
            reason = _determine_disable_reason(bs, actor, cid, sk, tf, actor_team, a_gid)
            out.append(TargetOption(target_id=cid, enabled=False, reason=reason))

    return sorted(out, key=lambda x: str(x.target_id))


def _determine_disable_reason(
    bs: BattleState,
    actor: CombatantID,
    target: CombatantID,
    sk: Skill,
    tf: str,
    actor_team: str,
    a_gid,
) -> TargetReason:
    """대상이 후보에서 제외된 이유를 판별."""
    target_team = bs.combatants[target].team

    # target_filter 불일치 체크
    if tf == "SELF" and target != actor:
        return "WRONG_TEAM"
    if tf == "ALLY" and target_team != actor_team:
        return "WRONG_TEAM"
    if tf == "ENEMY" and target_team == actor_team:
        return "WRONG_TEAM"

    # 나머지는 range 문제
    return "OUT_OF_RANGE"