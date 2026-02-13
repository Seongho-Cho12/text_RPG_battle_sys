# battle_system/engine/engine.py

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional
import uuid

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import BattleState, CharacterDef, CombatantState
from battle_system.core.commands import Step, Skill, ActionType
from battle_system.formation.movement import engage, disengage
from battle_system.formation.reactions import reaction_attack_candidates
from battle_system.rules.basic_attack import basic_attack, execute_reaction_attacks
from battle_system.rules.indices.facade import IndexModifiers
from battle_system.initiative.ordering import compute_turn_order
from battle_system.rules.checks import roll_status_success
from battle_system.rules.indices.status import compute_status_resist_index
from battle_system.timebase.durations import turns_to_ticks_for_battle
from battle_system.core.models import ModifierInstance, ModifierKey
from battle_system.rules.indices.crit import CritStat

DISPEL_INFLICT = 20

@dataclass(frozen=True)
class EngineOutcome:
    """
    최소 엔진 결과.
    - events는 사람이 읽기 위한 로그 문자열 (테스트/리포트용)
    """
    events: List[str]


@dataclass(frozen=True)
class BattleConfig:
    ally_group_id: GroupID = GroupID(0)
    enemy_group_id: GroupID = GroupID(1)

    
# def _sum_status_inflict_mod(bs: BattleState, cid: CombatantID) -> int:
#     st = bs.combatants[cid]
#     s = 0
#     for m in st.modifiers:
#         if m.key == "STATUS_INFLICT":
#             s += int(m.delta)
#     return s

# def _sum_status_resist_mod(bs: BattleState, cid: CombatantID) -> int:
#     st = bs.combatants[cid]
#     s = 0
#     for m in st.modifiers:
#         if m.key == "STATUS_RESIST":
#             s += int(m.delta)
#     return s


class BattleEngine:
    def __init__(self, config: BattleConfig | None = None) -> None:
        self.config = config or BattleConfig()

    def create_battle(self, allies: List[CharacterDef], enemies: List[CharacterDef]) -> BattleState:
        defs: Dict[CombatantID, CharacterDef] = {}
        combatants: Dict[CombatantID, CombatantState] = {}

        for d in allies + enemies:
            if d.cid in defs:
                raise ValueError(f"Duplicate cid: {d.cid}")
            defs[d.cid] = d

        groups: Dict[GroupID, List[CombatantID]] = {
            self.config.ally_group_id: [],
            self.config.enemy_group_id: [],
        }

        for d in allies:
            groups[self.config.ally_group_id].append(d.cid)
            combatants[d.cid] = CombatantState(
                cid=d.cid, team="ALLY", _hp=d.max_hp, max_hp=d.max_hp, group_id=self.config.ally_group_id
            )

        for d in enemies:
            groups[self.config.enemy_group_id].append(d.cid)
            combatants[d.cid] = CombatantState(
                cid=d.cid, team="ENEMY", _hp=d.max_hp, max_hp=d.max_hp, group_id=self.config.enemy_group_id
            )

        turn_order = compute_turn_order(defs)

        bs = BattleState(
            defs=defs,
            combatants=combatants,
            turn_order=turn_order,
            turn_index=0,
            tick=0,
            groups=groups,
        )

        self._reset_turn_slots(bs, bs.current_actor_id())
        return bs

    def end_turn(self, bs: BattleState) -> EngineOutcome:
        """
        턴 종료:
        - tick += 1  (전역 tick)
        - DoT/HoT 등 주기 효과 적용
        - 모든 전투 참가자의 cooldown/effects를 1 감소 (0 이하면 제거)
        - 다음 액터로 넘어감
        - 슬롯 리셋
        """
        bs.tick += 1
        events = self._apply_periodic_effects(bs)
        self._tick_decrement_all(bs)

        bs.turn_index = (bs.turn_index + 1) % len(bs.turn_order)
        self._reset_turn_slots(bs, bs.current_actor_id())

        self._check_battle_end(bs)

        return EngineOutcome(events=events)

    def apply_skill(self, bs: BattleState, skill: Skill) -> EngineOutcome:
        """
        스킬 단위 실행 파이프라인.
        - 슬롯(MAIN/SUB) 소모
        - 쿨다운 확인/등록
        - steps 순차 실행(각 step은 미시 행동)
        """
        events: list[str] = []

        # 0) 현재 턴 actor 확인
        actor = bs.current_actor_id()
        if skill.actor != actor:
            raise ValueError("Not your turn (skill.actor != current actor).")

        # 1) 슬롯 소모
        if skill.action_type == "MAIN":
            self._use_main(bs, actor)
            events.append(f"SLOT: MAIN used by {actor}")
        else:
            self._use_sub(bs, actor)
            events.append(f"SLOT: SUB used by {actor}")

        # 2) 쿨다운 체크
        if skill.cooldown_turns > 0:
            left = bs.combatants[actor].cooldowns.get(skill.skill_id, 0)
            if left > 0:
                raise ValueError(f"Skill on cooldown: {skill.skill_id} (ticks_left={left})")
            
        # 3) 상태이상에 의한 스킬 사용 불가 체크
        ok, reason = self._can_use_skill_due_to_effects(bs, actor, skill)
        if not ok:
            # 기존 Outcome 생성 방식에 맞춰 반환값만 맞춰줘
            events = []
            events.append(f"SKILL_BLOCKED_BY_EFFECT: actor={actor} skill={skill.skill_id} reason={reason}")
            return EngineOutcome(events=events)

        # 3.5) Phase 37: 아이템 소모 (모든 입력 확정 후, step 실행 직전)
        if skill.consume_item_id is not None:
            item_id = skill.consume_item_id
            consumed = self._inv_snapshot_consume_one(bs, actor, item_id)
            if not consumed:
                raise ValueError(
                    f"Cannot consume item '{item_id}': insufficient quantity "
                    f"(actor={actor}). UI should have prevented this."
                )
            self._inv_delta_add(bs, actor, item_id, -1)
            events.append(f"ITEM_CONSUMED: actor={actor} item={item_id}")

        # 4) step 실행
        steps = skill.steps or []
        prev: int = 1  # 첫 step은 기본 실행 가능
        for s in steps:
            # 1) 조건 미달이면 이후 step 전부 중단
            if prev < s.require_prev_gte:
                events.append(
                    f"STEP_SKIPPED: kind={s.kind} require_prev_gte={getattr(s, 'require_prev_gte', 0)} prev={prev}"
                )
                events.append("CHAIN_BREAK")
                break
            
            # 2) 전투가 종료되었는지 확인
            self._check_battle_end(bs)
            if bs.ended:
                break

            # 3) step 실행 -> result(정수) + events
            prev, step_events = self._apply_step(
                bs, actor=actor, s=s, crit_stat=skill.crit_stat
            )
            events.extend(step_events)

        # 4) 쿨다운 등록(스킬 실행 완료 후)
        if skill.cooldown_turns > 0:
            cd_ticks = turns_to_ticks_for_battle(bs, int(skill.cooldown_turns))
            bs.combatants[actor].cooldowns[skill.skill_id] = cd_ticks
            events.append(f"COOLDOWN_SET: {actor} skill={skill.skill_id} turns={skill.cooldown_turns} ticks={cd_ticks}")

        return EngineOutcome(events=events)


    # ----------------- internal -----------------

    def _assert_my_turn(self, bs: BattleState, actor: CombatantID) -> None:
        if actor != bs.current_actor_id():
            raise ValueError("Not your turn.")

    def _use_main(self, bs: BattleState, actor: CombatantID) -> None:
        self._assert_my_turn(bs, actor)
        st = bs.combatants[actor]
        if not st.can_main:
            raise ValueError("Main action already used this turn.")
        st.can_main = False

    def _use_sub(self, bs: BattleState, actor: CombatantID) -> None:
        self._assert_my_turn(bs, actor)
        st = bs.combatants[actor]
        if not st.can_sub:
            raise ValueError("Sub action already used this turn.")
        st.can_sub = False

    def _reset_turn_slots(self, bs: BattleState, actor: CombatantID) -> None:
        st = bs.combatants[actor]
        st.can_main = True
        st.can_sub = True

    def _tick_decrement_all(self, bs: BattleState) -> None:
        for st in bs.combatants.values():
            # cooldowns
            for k in list(st.cooldowns.keys()):
                st.cooldowns[k] -= 1
                if st.cooldowns[k] <= 0:
                    del st.cooldowns[k]

            # effects
            for k in list(st.effects.keys()):
                st.effects[k] -= 1
                if st.effects[k] <= 0:
                    del st.effects[k]

            # modifiers (list)
            if st.modifiers:
                new_list = []
                for m in st.modifiers:
                    m.ticks_left -= 1
                    if m.ticks_left > 0:
                        new_list.append(m)
                st.modifiers = new_list

    def _has_effect(self, bs: BattleState, cid: CombatantID, eff: str) -> bool:
        return bs.combatants[cid].effects.get(eff, 0) > 0


    def _skill_has_move(self, skill) -> bool:
        # MOVE 계열 step kind를 정확히 쓰면 더 좋지만, 지금은 안전하게 startswith로 처리
        for s in skill.steps:
            if isinstance(s.kind, str) and s.kind.startswith("MOVE"):
                return True
            if s.kind == "MOVE":
                return True
        return False


    def _skill_has_apply_effect(self, skill) -> bool:
        for s in skill.steps:
            if s.kind == "APPLY_EFFECT":
                return True
        return False


    def _is_magic_skill(self, skill) -> bool:
        crit_stat = getattr(skill, "crit_stat", "STR")
        return crit_stat in ("INT", "WIS")


    def _can_use_skill_due_to_effects(self, bs: BattleState, actor: CombatantID, skill) -> tuple[bool, str]:
        """
        Phase 26 컨트롤/제한 상태이상 적용.
        - STUN/PARALYSIS/FROZEN: 행동 불가
        - BIND: 이동 포함 스킬 사용 불가
        - CURSE: 마법 공격 + 상태이상 부여 포함 스킬 사용 불가
        - OBLIVION: 2스텝 이상 스킬 사용 불가
        """
        # 행동 불가
        if self._has_effect(bs, actor, "STUN"):
            return False, "STUN: action blocked"
        if self._has_effect(bs, actor, "PARALYSIS"):
            return False, "PARALYSIS: action blocked"
        if self._has_effect(bs, actor, "FROZEN"):
            return False, "FROZEN: action blocked"

        # BIND: 이동 포함 스킬 금지
        if self._has_effect(bs, actor, "BIND") and self._skill_has_move(skill):
            return False, "BIND: move skill blocked"

        # OBLIVION: 2스텝 이상 스킬 금지
        if self._has_effect(bs, actor, "OBLIVION") and len(skill.steps) >= 2:
            return False, "OBLIVION: multi-step skill blocked"

        # CURSE: "마법 공격" or "상태이상 부여 포함" 스킬 금지
        if self._has_effect(bs, actor, "CURSE"):
            if self._is_magic_skill(skill) or self._skill_has_apply_effect(skill):
                return False, "CURSE: magic or apply_effect skill blocked"

        return True, ""
    
    def _apply_periodic_effects(self, bs: BattleState) -> list[str]:
        """
        Phase 27: DoT/HoT 적용(단순 나머지 방식)

        - Turn-based (출혈/중독):
            bs.tick % participant_count == 1
        - Tick-based (부패):
            bs.tick % 3 == 1
        """
        events: list[str] = []

        n = len(bs.turn_order)
        if n <= 0:
            return events

        turn_boundary = (bs.tick % n == 1)
        decay_boundary = (bs.tick % 3 == 1)

        for cid, st in bs.combatants.items():
            if st.hp <= 0:
                continue

            # --- turn-based ---
            if turn_boundary:
                if st.effects.get("BLEEDING", 0) > 0:
                    before = st.hp
                    st.hp = max(0, before - 1)
                    events.append(
                        f"DOT_TICK: tick={bs.tick} cid={cid} effect=BLEEDING dmg=1 hp={before}->{st.hp}"
                    )

                if st.effects.get("POISONED", 0) > 0:
                    before = st.hp
                    st.hp = max(0, before - 1)
                    events.append(
                        f"DOT_TICK: tick={bs.tick} cid={cid} effect=POISONED dmg=1 hp={before}->{st.hp}"
                    )

            # --- tick-based ---
            if decay_boundary and st.effects.get("DECAY", 0) > 0:
                before = st.hp
                st.hp = max(0, before - 2)
                events.append(
                    f"DOT_TICK: tick={bs.tick} cid={cid} effect=DECAY dmg=2 hp={before}->{st.hp}"
                )

        return events
    
    def _apply_instant_death(self, bs: BattleState, *, actor: CombatantID, tgt: CombatantID, events: list[str]) -> None:
        before = bs.combatants[tgt].hp
        bs.combatants[tgt].hp = 0
        events.append(f"INSTANT_DEATH: {actor}->{tgt} hp={before}->0")

    def _sum_status_tag_mod(self, bs: BattleState, cid: CombatantID, *, key: ModifierKey, status_id: str) -> int:
        """
        STATUS_RESIST / STATUS_INFLICT modifier 합산.
        - tag가 status_id와 동일하거나 "ALL"인 것만 합산.
        """
        st = bs.combatants[cid]
        total = 0
        for m in st.modifiers:
            if m.key != key:
                continue
            tag = getattr(m, "status_tag", None)
            if tag is None:
                continue
            if tag == "ALL" or tag == status_id:
                total += int(m.delta)
        return total
    
    def _sum_mod(self, bs: BattleState, cid: CombatantID, *, key: ModifierKey) -> int:
        total = 0
        for m in bs.combatants[cid].modifiers:
            if m.key == key:
                total += int(m.delta)
        return total
    
    def _effective_stat(self, bs: BattleState, cid: CombatantID, stat: str) -> int:
        base = getattr(bs.defs[cid].stats, stat.lower())  # "agi" / "wis"
        delta = 0
        for m in bs.combatants[cid].modifiers:
            if m.key == stat:
                delta += int(m.delta)
        return max(0, int(base + delta))

    def _enemy_wis_avg_with_resist(self, bs: BattleState, actor: CombatantID, *, resist_key: ModifierKey) -> int:
        actor_team = bs.combatants[actor].team
        enemies = [cid for cid, st in bs.combatants.items() if st.team != actor_team and not st.is_down]
        if not enemies:
            return 0

        vals = []
        for e in enemies:
            wis = self._effective_stat(bs, e, "WIS")
            wis += self._sum_mod(bs, e, key=resist_key)  # 개인별 resist mod
            vals.append(wis)

        return int(sum(vals) / len(vals))  # 내림 평균
    
    def _throw_instant_mods(self, bs: BattleState, actor: CombatantID, weight: int) -> IndexModifiers:
        # 가능하면 유효 STR(스탯 modifier 반영) 사용
        str_stat = bs.defs[actor].stats.str  # 최소 버전: 지금 구조에 맞게 접근
        q = int(str_stat / 4)

        if weight == 0:
            return IndexModifiers(hit=-5, weak=30)
        if weight == 1:
            return IndexModifiers(weak=10)
        if weight == 2:
            return IndexModifiers()
        if weight == 4:
            return IndexModifiers(weak=-10)
        if weight == 8:
            return IndexModifiers(hit=(-10 + q), weak=-10, critical=2)
        if weight == 16:
            return IndexModifiers(hit=(-25 + q), weak=-20, critical=5)

        raise ValueError(f"invalid throw weight: {weight}")
    
    def _inv_delta_add(self, bs, cid, item_id, delta):
        d = bs.inventory_delta.setdefault(cid, {})
        d[item_id] = d.get(item_id, 0) + delta
        if d[item_id] == 0:
            del d[item_id]

    def _inv_snapshot_consume_one(self, bs, cid, item_id) -> bool:
        inv = bs.inventory_snapshot.get(cid, {})
        if inv.get(item_id, 0) <= 0:
            return False
        inv[item_id] -= 1
        if inv[item_id] <= 0:
            del inv[item_id]
        return True
    
    def _check_battle_end(self, bs: BattleState) -> None:
        if bs.ended:
            return  # ESCAPE 등 이미 종료된 경우 유지

        any_ally_alive = any(st.team == "ALLY" and not st.is_down for st in bs.combatants.values())
        any_enemy_alive = any(st.team == "ENEMY" and not st.is_down for st in bs.combatants.values())

        if not any_enemy_alive and any_ally_alive:
            bs.ended = True
            bs.end_reason = "ALLY_VICTORY"
            return
        if not any_ally_alive:
            bs.ended = True
            bs.end_reason = "ENEMY_VICTORY"
            return

    def _apply_step(self, bs: BattleState, *, actor: CombatantID, s: Step, crit_stat: CritStat) -> tuple[int, list[str]]:
        events: list[str] = []
        prev_gid = bs.combatants[actor].group_id
        result: int = 1
        anchor = self._resolve_anchor(bs, s)

        if s.kind == "MOVE_ENGAGE":
            if s.target is None:
                raise ValueError("MOVE_ENGAGE requires target")
            engage(bs, actor=actor, target=s.target)
            events.append(f"STEP: MOVE_ENGAGE {actor}->{s.target}")

            stealth = bs.combatants[actor].effects.get("STEALTH", 0) > 0
            reaction_immune = s.reaction_immune or stealth

            cands = reaction_attack_candidates(
                bs, mover=actor, prev_group_id=prev_gid, reaction_immune=reaction_immune
            )
            events.extend(self._run_reactions(bs, mover=actor, cands=cands, reaction_hit_penalty=s.reaction_hit_penalty))

        elif s.kind == "MOVE_DISENGAGE":
            new_gid = disengage(bs, actor=actor)
            events.append(f"STEP: MOVE_DISENGAGE {actor} -> new_group={new_gid}")

            stealth = bs.combatants[actor].effects.get("STEALTH", 0) > 0
            reaction_immune = s.reaction_immune or stealth

            cands = reaction_attack_candidates(
                bs, mover=actor, prev_group_id=prev_gid, reaction_immune=reaction_immune
            )
            events.extend(self._run_reactions(bs, mover=actor, cands=cands, reaction_hit_penalty=s.reaction_hit_penalty))

        elif s.kind == "ATTACK":
            # anchor/target 규칙
            if s.target is None and s.area != "ALL":
                raise ValueError("ATTACK requires target unless area == 'ALL'")

            # ✅ 사거리 체크(근/원/무관)
            if not self._check_range(bs, actor=actor, anchor=anchor, s=s):
                events.append(f"OUT_OF_RANGE: ATTACK actor={actor} anchor={anchor} range={s.range} area={s.area}")
                return 0, events

            # ✅ 범위 확장(SINGLE/GROUP/ALL)
            targets = self._resolve_targets(bs, anchor=anchor, s=s)
            if not targets:
                events.append(f"NO_TARGETS: ATTACK actor={actor} anchor={anchor} range={s.range} area={s.area}")
                return 0, events

            outcome_rank = {"EVADE": 0, "WEAK": 1, "STRONG": 2, "CRITICAL": 3}
            best = 0

            mods = getattr(s, "attack_modifiers", IndexModifiers())

            for tgt in targets:
                r = basic_attack(bs, attacker=actor, defender=tgt, modifiers=mods, crit_stat=crit_stat)
                if bs.combatants[actor].effects.get("STEALTH", 0) > 0 and r.get("hit", False):
                    del bs.combatants[actor].effects["STEALTH"]
                    events.append(f"STEALTH_BROKEN: {actor} (hit_success)")
                outcome = r["outcome"]
                events.append(f"STEP: ATTACK {actor}->{tgt} outcome={outcome} dmg={r['damage']}")
                best = max(best, int(outcome_rank.get(outcome, 0)))

            result = best


        elif s.kind == "APPLY_EFFECT":
            if s.target is None and s.area != "ALL":
                raise ValueError("APPLY_EFFECT requires target unless area == 'ALL'")
            if not s.effect_id or s.effect_duration is None:
                raise ValueError("APPLY_EFFECT requires effect_id/effect_duration(turns)")
            if s.status_inflict is None:
                raise ValueError("APPLY_EFFECT requires status_inflict")

            # ✅ 사거리 체크(근/원/무관)
            if not self._check_range(bs, actor=actor, anchor=anchor, s=s):
                events.append(
                    f"OUT_OF_RANGE: APPLY_EFFECT actor={actor} anchor={anchor} effect={s.effect_id} range={s.range} area={s.area}"
                )
                return 0, events

            # ✅ 범위 확장(SINGLE/GROUP/ALL)
            targets = self._resolve_targets(bs, anchor=anchor, s=s)
            if not targets:
                events.append(
                    f"NO_TARGETS: APPLY_EFFECT actor={actor} anchor={anchor} effect={s.effect_id} range={s.range} area={s.area}"
                )
                return 0, events

            eff = s.effect_id
            dur_ticks = turns_to_ticks_for_battle(bs, int(s.effect_duration))

            success_any = 0

            for tgt in targets:
                base_inflict = int(s.status_inflict)
                base_resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)

                inflict_bonus = self._sum_status_tag_mod(bs, actor, key="STATUS_INFLICT", status_id=eff)
                resist_bonus = self._sum_status_tag_mod(bs, tgt, key="STATUS_RESIST", status_id=eff)

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
                    self._apply_instant_death(bs, actor=actor, tgt=tgt, events=events)
                    success_any = 1
                else:
                    sr = roll_status_success(
                        inflict=base_inflict + inflict_bonus,
                        resist=int(base_resist.value) + resist_bonus,
                    )
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

            result = 1 if success_any else 0

        elif s.kind == "REMOVE_EFFECT":
            if s.target is None and s.area != "ALL":
                raise ValueError("REMOVE_EFFECT requires target unless area == 'ALL'")
            if not s.effect_id:
                raise ValueError("REMOVE_EFFECT requires effect_id")

            # ✅ 사거리 체크
            if not self._check_range(bs, actor=actor, anchor=anchor, s=s):
                events.append(
                    f"OUT_OF_RANGE: REMOVE_EFFECT actor={actor} anchor={anchor} effect={s.effect_id} range={s.range} area={s.area}"
                )
                return 0, events

            # ✅ 범위 확장
            targets = self._resolve_targets(bs, anchor=anchor, s=s)
            if not targets:
                events.append(
                    f"NO_TARGETS: REMOVE_EFFECT actor={actor} anchor={anchor} effect={s.effect_id} range={s.range} area={s.area}"
                )
                return 0, events

            eff = s.effect_id
            success_any = 0

            for tgt in targets:
                if eff not in bs.combatants[tgt].effects:
                    events.append(f"EFFECT_REMOVE_NOOP: {tgt} has_no {eff}")
                    continue

                resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)
                resist_bonus = self._sum_status_tag_mod(bs, tgt, key="STATUS_RESIST", status_id=eff)

                if not resist.resistible:
                    # 저항 불가 => 해제 불가(자동 실패)
                    events.append(
                        f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                        f"inflict={DISPEL_INFLICT} resist=NA resistible=False roll=NA success=True"
                    )
                    events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
                else:
                    sr = roll_status_success(
                        inflict=int(DISPEL_INFLICT),
                        resist=int(resist.value) + resist_bonus,
                    )
                    events.append(
                        f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                        f"inflict={DISPEL_INFLICT} resist={resist.value}+{resist_bonus} resistible=True roll={sr.roll} success={sr.success}"
                    )
                    if sr.success:
                        # success=True => '걸린다' => 해제 실패
                        events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
                    else:
                        del bs.combatants[tgt].effects[eff]
                        events.append(f"DISPEL_SUCCESS: {tgt} -{eff}")
                        success_any = 1

            result = 1 if success_any else 0


        elif s.kind == "APPLY_MODIFIER":
            if s.target is None and s.area != "ALL":
                raise ValueError("APPLY_MODIFIER requires target unless area == 'ALL'")
            if s.modifier_key is None or s.modifier_delta is None or s.modifier_duration is None:
                raise ValueError("APPLY_MODIFIER requires modifier_key/modifier_delta/modifier_duration")

            # ✅ 사거리 체크
            if not self._check_range(bs, actor=actor, anchor=anchor, s=s):
                events.append(
                    f"OUT_OF_RANGE: APPLY_MODIFIER actor={actor} anchor={anchor} key={s.modifier_key} range={s.range} area={s.area}"
                )
                return 0, events

            # ✅ 범위 확장
            targets = self._resolve_targets(bs, anchor=anchor, s=s)
            if not targets:
                events.append(
                    f"NO_TARGETS: APPLY_MODIFIER actor={actor} anchor={anchor} key={s.modifier_key} range={s.range} area={s.area}"
                )
                return 0, events

            dur_ticks = turns_to_ticks_for_battle(bs, int(s.modifier_duration))
            applied_any = 0

            for tgt in targets:
                mid = uuid.uuid4().hex
                tag = None
                if s.modifier_key in ("STATUS_RESIST", "STATUS_INFLICT"):
                    if not getattr(s, "modifier_status_tag", None):
                        raise ValueError("APPLY_MODIFIER: STATUS_RESIST/STATUS_INFLICT requires modifier_status_tag ('ALL' or StatusID)")
                    tag = str(s.modifier_status_tag)
                mi = ModifierInstance(
                    mid=mid,
                    key=s.modifier_key,
                    delta=int(s.modifier_delta),
                    ticks_left=dur_ticks,
                    status_tag=tag,
                )
                bs.combatants[tgt].modifiers.append(mi)
                events.append(
                    f"MOD_APPLIED: {tgt} mid={mid} key={s.modifier_key} delta={mi.delta} "
                    f"turns={s.modifier_duration} ticks={dur_ticks} tag={tag}"
                )
                applied_any = 1

            result = 1 if applied_any else 0


        elif s.kind == "APPLY_HP_DELTA":
            if s.target is None and s.area != "ALL":
                raise ValueError("APPLY_HP_DELTA requires target unless area == 'ALL'")
            if s.hp_delta is None:
                raise ValueError("APPLY_HP_DELTA requires hp_delta")

            # ✅ 사거리 체크
            if not self._check_range(bs, actor=actor, anchor=anchor, s=s):
                events.append(
                    f"OUT_OF_RANGE: APPLY_HP_DELTA actor={actor} anchor={anchor} delta={int(s.hp_delta)} range={s.range} area={s.area}"
                )
                return 0, events

            # ✅ 범위 확장
            targets = self._resolve_targets(bs, anchor=anchor, s=s)
            if not targets:
                events.append(
                    f"NO_TARGETS: APPLY_HP_DELTA actor={actor} anchor={anchor} delta={int(s.hp_delta)} range={s.range} area={s.area}"
                )
                return 0, events

            for tgt in targets:
                before = bs.combatants[tgt].hp
                bs.combatants[tgt].hp = before + int(s.hp_delta)
                after = bs.combatants[tgt].hp
                events.append(f"HP_DELTA: {tgt} {before}->{after} (delta={int(s.hp_delta)})")

            result = 1
        
        elif s.kind == "TACTICAL_STEALTH":
            if s.effect_duration is None:
                raise ValueError("TACTICAL_STEALTH requires effect_duration(turns)")

            inflict = self._effective_stat(bs, actor, "AGI") + self._sum_mod(bs, actor, key="STEALTH_INFLICT")
            resist  = self._enemy_wis_avg_with_resist(bs, actor, resist_key="STEALTH_RESIST")

            sr = roll_status_success(inflict=inflict, resist=resist)
            events.append(f"STEALTH_CHECK: actor={actor} inflict={inflict} resist={resist} roll={sr.roll} success={sr.success}")

            if sr.success:
                dur_ticks = turns_to_ticks_for_battle(bs, int(s.effect_duration))
                prev = bs.combatants[actor].effects.get("STEALTH", 0)
                bs.combatants[actor].effects["STEALTH"] = prev + dur_ticks
                events.append(f"STEALTH_APPLIED: {actor} turns={s.effect_duration} ticks=+{dur_ticks} total_ticks={prev+dur_ticks}")
                result = 1
            else:
                result = 0

        elif s.kind == "TACTICAL_ESCAPE":
            inflict = (
                self._effective_stat(bs, actor, "AGI")
                + self._effective_stat(bs, actor, "WIS")
                + self._sum_mod(bs, actor, key="ESCAPE_INFLICT")
            )
            if bs.combatants[actor].effects.get("STEALTH", 0) > 0:
                inflict += 10

            resist = self._enemy_wis_avg_with_resist(bs, actor, resist_key="ESCAPE_RESIST")

            sr = roll_status_success(inflict=inflict, resist=resist)
            events.append(f"ESCAPE_CHECK: actor={actor} inflict={inflict} resist={resist} roll={sr.roll} success={sr.success}")

            if sr.success:
                bs.ended = True
                bs.end_reason = "ESCAPE"
                events.append("BATTLE_ENDED: ESCAPE")
                result = 1
            else:
                result = 0
            
        elif s.kind == "TACTICAL_DETECT_STEALTH":
            # target은 1명만 의미있음(감지는 개인 지정)
            if s.target is None:
                return 0, ["DETECT_STEALTH: missing target"]

            tgt = s.target

            # 타겟이 다운/없음 방어
            if tgt not in bs.combatants or bs.combatants[tgt].is_down:
                events.append(f"DETECT_STEALTH: invalid target {tgt}")
                return 0, events

            # 타겟이 은신 상태가 아니면 감지 시도할 필요 없음
            if bs.combatants[tgt].effects.get("STEALTH", 0) <= 0:
                events.append(f"DETECT_STEALTH: target {tgt} not stealth")
                return 0, events

            same_group = (bs.combatants[actor].group_id == bs.combatants[tgt].group_id)

            tgt_agi = self._effective_stat(bs, tgt, "AGI")
            # inflict: 타겟(은신자) 기준
            base_inflict = (tgt_agi // 2) if same_group else tgt_agi
            inflict = base_inflict + self._sum_mod(bs, tgt, key="STEALTH_INFLICT")

            # resist: 시전자(감지자) 기준
            actor_wis = self._effective_stat(bs, actor, "WIS")
            base_resist = int(actor_wis * 1.5)
            resist = base_resist + self._sum_mod(bs, actor, key="STEALTH_RESIST")

            sr = roll_status_success(inflict=inflict, resist=resist)

            # success=True  => 은신 성공(감지 실패)
            # success=False => 은신 실패(감지 성공)  <-- REMOVE_EFFECT와 동일한 반전 구조
            if not sr.success:
                # 감지 성공: 은신 해제
                del bs.combatants[tgt].effects["STEALTH"]
                events.append(
                    f"DETECT_STEALTH_CHECK: actor={actor} target={tgt} same_group={same_group} "
                    f"inflict={inflict} resist={resist} roll={sr.roll} stealth_success={sr.success}",
                    f"DETECT_STEALTH_SUCCESS: target={tgt} stealth_removed",
                )
                result = 1
            else:
                events.append(
                    f"DETECT_STEALTH_CHECK: actor={actor} target={tgt} same_group={same_group} "
                    f"inflict={inflict} resist={resist} roll={sr.roll} stealth_success={sr.success}",
                    f"DETECT_STEALTH_FAIL: target={tgt} remains_stealth",
                )
                result = 0
            
        elif s.kind == "TACTICAL_THROW":
            if not s.throw_item_id:
                events.append("THROW: missing throw_item_id")
                return 0, events

            item_id = s.throw_item_id

            if item_id not in bs.items:
                events.append(f"THROW: unknown item_id {item_id}")
                return 0, events

            # ✅ 사거리 체크(근/원/무관)
            if not self._check_range(bs, actor=actor, anchor=anchor, s=s):
                events.append(f"OUT_OF_RANGE: ATTACK actor={actor} anchor={anchor} range={s.range} area={s.area}")
                return 0, events

            # ✅ 범위 확장(SINGLE/GROUP/ALL)
            targets = self._resolve_targets(bs, anchor=anchor, s=s)
            if not targets:
                events.append(f"NO_TARGETS: ATTACK actor={actor} anchor={anchor} range={s.range} area={s.area}")
                return 0, events
            
            if not self._inv_snapshot_consume_one(bs, actor, item_id):
                events.append(f"THROW: item not available {item_id}")
                return 0, events
            
            self._inv_delta_add(bs, actor, item_id, -1)

            w = bs.items[item_id].weight
            throw_mods = self._throw_instant_mods(bs, actor, w)

            step_mods = s.attack_modifiers or IndexModifiers()
            combined = IndexModifiers(
                hit=throw_mods.hit + step_mods.hit,
                evade=throw_mods.evade + step_mods.evade,
                weak=throw_mods.weak + step_mods.weak,
                strong=throw_mods.strong + step_mods.strong,
                critical=throw_mods.critical + step_mods.critical,
            )

            events.append(f"THROW_CONSUME: actor={actor} item={item_id} weight={w} mods={combined}")
            outcome_rank = {"EVADE": 0, "WEAK": 1, "STRONG": 2, "CRITICAL": 3}
            best = 0

            mods = getattr(s, "attack_modifiers", IndexModifiers())

            for tgt in targets:
                r = basic_attack(bs, attacker=actor, defender=tgt, modifiers=combined, crit_stat="STR")
                if bs.combatants[actor].effects.get("STEALTH", 0) > 0 and r.get("hit", False):
                    del bs.combatants[actor].effects["STEALTH"]
                    events.append(f"STEALTH_BROKEN: {actor} (hit_success)")
                outcome = r["outcome"]
                events.append(f"STEP: THROW_ATTACK {actor}->{tgt} outcome={outcome} dmg={r['damage']}")
                best = max(best, int(outcome_rank.get(outcome, 0)))

            result = best

        else:
            raise ValueError(f"Unknown Step.kind: {s.kind}")

        return result, events
    
    def _resolve_anchor(self, bs: BattleState, s: Step) -> Optional[CombatantID]:
        if s.area == "ALL":
            return s.target  # 없어도 됨
        if s.target is None:
            raise ValueError("Step.target is required unless area == 'ALL'")
        return s.target

    def _check_range(self, bs: BattleState, actor: CombatantID, anchor: Optional[CombatantID], s: Step) -> bool:
        if s.range == "ANY":
            return True
        if anchor is None:
            # ALL인데 target 없는 경우: MELEE/RANGED는 의미가 없으니 막는게 안전
            return False
        a_gid = bs.combatants[actor].group_id
        t_gid = bs.combatants[anchor].group_id
        if s.range == "MELEE":
            return a_gid == t_gid
        if s.range == "RANGED":
            return a_gid != t_gid
        return True
    
    def _resolve_targets(self, bs: BattleState, anchor: Optional[CombatantID], s: Step) -> list[CombatantID]:
        if s.area == "ALL":
            return list(bs.combatants.keys())

        # SINGLE / GROUP 는 anchor 필수
        assert anchor is not None

        if s.area == "SINGLE":
            return [anchor]

        if s.area == "GROUP":
            anchor_state = bs.combatants[anchor]
            gid = anchor_state.group_id
            team = anchor_state.team  # "ALLY" or "ENEMY"

            # 같은 그룹이더라도 팀이 섞일 수 있으니 "anchor와 같은 팀"만 적용
            return [cid for cid in bs.groups.get(gid, []) if bs.combatants[cid].team == team]

        raise ValueError(f"Unknown area: {s.area}")

    def _run_reactions(
        self,
        bs: BattleState,
        *,
        mover: CombatantID,
        cands: list[CombatantID],
        reaction_hit_penalty: int,
    ) -> list[str]:
        events: list[str] = []
        if not cands:
            events.append("REACTION: none")
            return events

        events.append(f"REACTION: candidates={list(map(str, cands))}")
        results = execute_reaction_attacks(
            bs, mover=mover, candidates=cands, reaction_hit_penalty=reaction_hit_penalty
        )
        for atk_id, r in results.items():
            events.append(f"REACTION_ATTACK: {atk_id}->{mover} outcome={r['outcome']} dmg={r['damage']}")
        return events

