# battle_system/app/skill_ui.py
from __future__ import annotations

from dataclasses import dataclass, replace, field
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
class TargetSlot:
    """UI가 선택해야 하는 대상 슬롯 1개."""
    slot_name: str                    # "T1", "T2", ...
    target_filter: str                # ENEMY / ALLY / ANY / SELF
    candidates: List[CombatantID]     # 선택 가능 후보


@dataclass(frozen=True)
class SkillInputSpec:
    target_required: bool
    item_required: bool
    target_candidates: List[CombatantID]  # T1 후보 (하위 호환)
    item_candidates: List[str]
    target_slots: List[TargetSlot] = field(default_factory=list)  # 순서대로 선택


@dataclass(frozen=True)
class SkillAvailability:
    usable: bool
    reason: AvailabilityReason
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
    item_required = False

    # --- 슬롯 수집 ---
    seen_slots: dict[str, int] = {}  # slot_name -> 처음 등장 step index
    slot_filters: dict[str, str] = {}  # slot_name -> target_filter
    slot_step_indices: dict[str, List[int]] = {}  # slot_name -> step indices

    for i, s in enumerate(sk.steps or []):
        if s.kind == "TACTICAL_THROW":
            item_required = True

        if not _step_needs_target(s):
            continue

        slot = _resolve_slot_name(s.target)
        if slot is None:  # "SELF" or non-target step
            continue

        if slot not in seen_slots:
            seen_slots[slot] = i
            # step-level filter > skill-level filter
            stf = s.step_target_filter or getattr(sk, "target_filter", "ANY")
            slot_filters[slot] = stf
            slot_step_indices[slot] = []
        slot_step_indices[slot].append(i)

    # --- 슬롯별 후보 계산 (위치 시뮬레이션 포함) ---
    steps = sk.steps or []
    target_slots: List[TargetSlot] = []
    for slot_name in sorted(seen_slots, key=lambda s: seen_slots[s]):
        first_step_idx = seen_slots[slot_name]
        tf = slot_filters[slot_name]
        # 이 슬롯 첫 등장 이전 step들의 MOVE 효과를 시뮬레이션
        predicted_gid = _simulate_group_after_steps(bs, actor, steps[:first_step_idx], seen_slots)
        candidates = _compute_slot_candidates(bs, actor, sk, steps, slot_step_indices[slot_name], tf, predicted_gid)
        target_slots.append(TargetSlot(slot_name=slot_name, target_filter=tf, candidates=candidates))

    # --- 아이템 후보 ---
    item_candidates: List[str] = []
    if item_required:
        snap = bs.inventory_snapshot.get(actor, {})
        item_candidates = [item_id for item_id, cnt in snap.items() if int(cnt) > 0]

    target_required = len(target_slots) > 0
    target_candidates = target_slots[0].candidates if target_slots else []

    return SkillInputSpec(
        target_required=target_required,
        item_required=item_required,
        target_candidates=target_candidates,
        item_candidates=item_candidates,
        target_slots=target_slots,
    )


def instantiate_skill_with_inputs(
    sk: Skill,
    *,
    target: Optional[CombatantID] = None,
    targets: Optional[Dict[str, CombatantID]] = None,
    throw_item_id: Optional[str] = None,
) -> Skill:
    """
    UI 입력을 반영해 실행용 Skill 인스턴스 생성.
    - 슬롯 이름(T1, T2, ...)을 실제 CombatantID로 치환
    - TACTICAL_THROW에 throw_item_id 채움
    - 하위 호환: target kwarg는 {"T1": target}으로 변환
    """
    # 하위 호환: 단일 target → dict
    if targets is None:
        targets = {}
    if target is not None and "T1" not in targets:
        targets["T1"] = target

    new_steps: List[Step] = []
    for s in (sk.steps or []):
        ns = s

        slot = _resolve_slot_name(s.target)
        if slot is not None and slot in targets:
            ns = replace(ns, target=targets[slot])

        if s.kind == "TACTICAL_THROW":
            if throw_item_id is None:
                raise ValueError("throw_item_id is required but not provided")
            ns = replace(ns, throw_item_id=throw_item_id)

        new_steps.append(ns)

    # 슬롯이 있는데 targets에 없으면 에러
    for s in (sk.steps or []):
        if not _step_needs_target(s):
            continue
        slot = _resolve_slot_name(s.target)
        if slot is not None and slot not in targets:
            raise ValueError(f"target for slot '{slot}' is required but not provided")

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
    return SkillInputSpec(False, False, [], [], [])


def _step_needs_target(s: Step) -> bool:
    """Step이 UI 대상 선택을 필요로 하는지 판별."""
    # SELF는 자동 해소, 선택 불필요
    if s.target == "SELF":
        return False
    if s.kind == "MOVE_ENGAGE":
        return True
    if s.kind in ("ATTACK", "APPLY_EFFECT", "REMOVE_EFFECT", "APPLY_MODIFIER", "APPLY_HP_DELTA", "DETECT_STEALTH"):
        return s.area != "ALL"
    return False


def _resolve_slot_name(target) -> Optional[str]:
    """target 값을 슬롯 이름으로 변환. SELF/None/CombatantID 구분."""
    if target is None:
        return "T1"  # null = T1 (하위 호환)
    if target == "SELF":
        return None  # 선택 불필요
    if isinstance(target, str) and target.startswith("T") and target[1:].isdigit():
        return target  # T1, T2, ...
    # 이미 CombatantID로 채워진 경우
    return None


def _simulate_group_after_steps(
    bs: BattleState,
    actor: CombatantID,
    steps: List[Step],
    resolved_slots: dict,
) -> Optional[object]:
    """
    주어진 step들을 실행했을 때 actor의 group_id를 예측.
    MOVE_ENGAGE → 대상의 group_id로 이동
    MOVE_DISENGAGE → 독립 group (None으로 표현)
    """
    predicted_gid = bs.combatants[actor].group_id
    for s in steps:
        if s.kind == "MOVE_ENGAGE":
            # 대상 슬롯이 이미 해소됐으면 그 대상의 group으로
            slot = _resolve_slot_name(s.target)
            if slot is not None and slot in resolved_slots:
                # 적 team의 아무 group이나 잡으면 됨
                actor_team = bs.combatants[actor].team
                for cid, cst in bs.combatants.items():
                    if not cst.is_down and cst.team != actor_team:
                        predicted_gid = cst.group_id
                        break
            elif s.target is not None and s.target != "SELF":
                # 이미 CombatantID로 채워진 경우
                tgt = s.target
                if tgt in bs.combatants:
                    predicted_gid = bs.combatants[tgt].group_id
        elif s.kind == "MOVE_DISENGAGE":
            predicted_gid = None  # type: ignore
    return predicted_gid


def _compute_slot_candidates(
    bs: BattleState,
    actor: CombatantID,
    sk: Skill,
    steps: List[Step],
    step_indices: List[int],
    target_filter: str,
    predicted_gid,
) -> List[CombatantID]:
    """
    슬롯의 후보 대상 계산.
    - target_filter로 ENEMY/ALLY/SELF/ANY 필터링
    - predicted_gid로 MELEE/RANGED range 판정
    """
    actor_team = bs.combatants[actor].team

    # 생존 풀
    if target_filter in ("SELF", "ALLY"):
        alive_pool = [cid for cid, st in bs.combatants.items() if not st.is_down]
    else:
        alive_pool = [cid for cid, st in bs.combatants.items() if not st.is_down and cid != actor]

    cands: set[CombatantID] = set()

    for idx in step_indices:
        s = steps[idx]

        if s.kind == "MOVE_ENGAGE":
            cands.update(alive_pool)
            continue

        if s.area == "ALL":
            continue

        if s.range == "ANY":
            cands.update(alive_pool)
        elif s.range == "MELEE":
            cands.update([cid for cid in alive_pool if bs.combatants[cid].group_id == predicted_gid])
        elif s.range == "RANGED":
            cands.update([cid for cid in alive_pool if bs.combatants[cid].group_id != predicted_gid])

    # target_filter 적용
    filtered: List[CombatantID] = []
    for cid in sorted(cands, key=lambda x: str(x)):
        if target_filter == "ANY":
            filtered.append(cid)
        elif target_filter == "SELF":
            if cid == actor:
                filtered.append(cid)
        elif target_filter == "ALLY":
            if bs.combatants[cid].team == actor_team:
                filtered.append(cid)
        elif target_filter == "ENEMY":
            if bs.combatants[cid].team != actor_team:
                filtered.append(cid)
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
    spec = compute_input_spec(bs, actor, sk)
    valid_set = set(spec.target_candidates)
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