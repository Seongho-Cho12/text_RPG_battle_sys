# battle_system/engine/engine.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import BattleState, CharacterDef, CombatantState
from battle_system.core.commands import Step, Skill
from battle_system.rules.basic_attack import basic_attack, execute_reaction_attacks
from battle_system.rules.indices.facade import IndexModifiers
from battle_system.initiative.ordering import compute_turn_order
from battle_system.timebase.durations import turns_to_ticks_for_battle
from battle_system.core.models import ModifierKey
from battle_system.rules.indices.crit import CritStat
from battle_system.engine.steps import apply_step

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
            # 1a) require_prev_gte 조건 미달이면 이후 step 전부 중단
            if prev < s.require_prev_gte:
                events.append(
                    f"STEP_SKIPPED: kind={s.kind} require_prev_gte={s.require_prev_gte} prev={prev}"
                )
                events.append("CHAIN_BREAK")
                break

            # 1b) require_prev_lte 조건 초과이면 이후 step 전부 중단
            if s.require_prev_lte is not None and prev > s.require_prev_lte:
                events.append(
                    f"STEP_SKIPPED: kind={s.kind} require_prev_lte={s.require_prev_lte} prev={prev}"
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
        return apply_step(self, bs, actor=actor, s=s, crit_stat=crit_stat)

    def _basic_attack(self, bs: BattleState, *, attacker: CombatantID, defender: CombatantID, modifiers, crit_stat):
        return basic_attack(bs, attacker=attacker, defender=defender, modifiers=modifiers, crit_stat=crit_stat)

    def _dispel_inflict(self) -> int:
        return DISPEL_INFLICT

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
