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

    def end_turn(self, bs: BattleState) -> None:
        """
        턴 종료:
        - tick += 1  (전역 tick)
        - 모든 전투 참가자의 cooldown/effects를 1 감소 (0 이하면 제거)
        - 다음 액터로 넘어감
        - 슬롯 리셋
        """
        bs.tick += 1
        self._tick_decrement_all(bs)

        bs.turn_index = (bs.turn_index + 1) % len(bs.turn_order)
        self._reset_turn_slots(bs, bs.current_actor_id())

    def apply_skill(self, bs: BattleState, skill: Skill, *, reaction_hit_penalty: int = 5) -> EngineOutcome:
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

        # 3) step 실행
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

            # 2) step 실행 -> result(정수) + events
            prev, step_events = self._apply_step(
                bs, actor=actor, s=s, reaction_hit_penalty=reaction_hit_penalty, crit_stat=skill.crit_stat
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

    def _apply_step(self, bs: BattleState, *, actor: CombatantID, s: Step, reaction_hit_penalty: int, crit_stat: CritStat) -> tuple[int, list[str]]:
        events: list[str] = []
        prev_gid = bs.combatants[actor].group_id
        result: int = 1
        anchor = self._resolve_anchor(bs, s)

        if s.kind == "MOVE_ENGAGE":
            if s.target is None:
                raise ValueError("MOVE_ENGAGE requires target")
            engage(bs, actor=actor, target=s.target)
            events.append(f"STEP: MOVE_ENGAGE {actor}->{s.target}")

            cands = reaction_attack_candidates(
                bs, mover=actor, prev_group_id=prev_gid, reaction_immune=s.reaction_immune
            )
            events.extend(self._run_reactions(bs, mover=actor, cands=cands, reaction_hit_penalty=reaction_hit_penalty))

        elif s.kind == "MOVE_DISENGAGE":
            new_gid = disengage(bs, actor=actor)
            events.append(f"STEP: MOVE_DISENGAGE {actor} -> new_group={new_gid}")

            cands = reaction_attack_candidates(
                bs, mover=actor, prev_group_id=prev_gid, reaction_immune=s.reaction_immune
            )
            events.extend(self._run_reactions(bs, mover=actor, cands=cands, reaction_hit_penalty=reaction_hit_penalty))

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

            for tgt in targets:
                r = basic_attack(bs, attacker=actor, defender=tgt, modifiers=IndexModifiers(), crit_stat=crit_stat)
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
                resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)

                if not resist.resistible:
                    prev = bs.combatants[tgt].effects.get(eff, 0)
                    bs.combatants[tgt].effects[eff] = prev + dur_ticks
                    events.append(
                        f"STATUS_CHECK: {actor}->{tgt} effect={eff} "
                        f"inflict={s.status_inflict} resist=NA resistible=False roll=NA success=True"
                    )
                    events.append(
                        f"EFFECT_APPLIED: {tgt} +{eff}(turns={s.effect_duration}, ticks=+{dur_ticks}, total_ticks={prev + dur_ticks})"
                    )
                    success_any = 1
                else:
                    sr = roll_status_success(inflict=int(s.status_inflict), resist=int(resist.value))
                    events.append(
                        f"STATUS_CHECK: {actor}->{tgt} effect={eff} "
                        f"inflict={s.status_inflict} resist={resist.value} resistible=True roll={sr.roll} success={sr.success}"
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

                if not resist.resistible:
                    # 저항 불가 => 해제 불가(자동 실패)
                    events.append(
                        f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                        f"inflict={DISPEL_INFLICT} resist=NA resistible=False roll=NA success=True"
                    )
                    events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
                else:
                    sr = roll_status_success(inflict=int(DISPEL_INFLICT), resist=int(resist.value))
                    events.append(
                        f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                        f"inflict={DISPEL_INFLICT} resist={resist.value} resistible=True roll={sr.roll} success={sr.success}"
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
                mi = ModifierInstance(
                    mid=mid,
                    key=s.modifier_key,
                    delta=int(s.modifier_delta),
                    ticks_left=dur_ticks,
                )
                bs.combatants[tgt].modifiers.append(mi)
                events.append(
                    f"MOD_APPLIED: {tgt} mid={mid} key={s.modifier_key} delta={mi.delta} turns={s.modifier_duration} ticks={dur_ticks}"
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

