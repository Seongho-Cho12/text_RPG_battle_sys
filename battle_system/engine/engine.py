# battle_system/engine/engine.py

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict
import uuid

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import BattleState, CharacterDef, CombatantState
from battle_system.core.commands import Step, ActionType
from battle_system.formation.movement import engage, disengage
from battle_system.formation.reactions import reaction_attack_candidates
from battle_system.rules.basic_attack import basic_attack, execute_reaction_attacks
from battle_system.rules.indices.facade import IndexModifiers
from battle_system.initiative.ordering import compute_turn_order
from battle_system.rules.checks import roll_status_success
from battle_system.rules.indices.status import compute_status_resist_index
from battle_system.timebase.durations import turns_to_ticks_for_battle
from battle_system.core.models import ModifierInstance, ModifierKey

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

    def apply_steps(self, bs: BattleState, steps: list[Step], *, reaction_hit_penalty: int = 5) -> EngineOutcome:
        events: list[str] = []
        actor = bs.current_actor_id()

        # ✅ 추가: 전투 불능이면 행동 스킵
        if bs.combatants[actor].is_down:
            events.append(f"SKIP_TURN: {actor} is DOWN")
            return EngineOutcome(events=events)

        # 0) 모든 step의 actor는 현재 actor여야 함(단일 턴 실행 전제)
        for s in steps:
            if s.actor != actor:
                raise ValueError("All steps must be executed by current actor.")

        # 1) action 슬롯 소모(steps 전체가 한 action을 공유한다고 가정)
        #    - 복합 스킬(이동+공격)도 MAIN 1회로 처리 가능
        action_type = steps[0].action_type if steps else "MAIN"
        if action_type == "MAIN":
            self._use_main(bs, actor)
            events.append("SLOT: MAIN used")
        else:
            self._use_sub(bs, actor)
            events.append("SLOT: SUB used")

        # 2) step 순차 실행
        for s in steps:
            events.extend(self._apply_step(bs, s, reaction_hit_penalty=reaction_hit_penalty))

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

    def _apply_step(self, bs: BattleState, s: Step, *, reaction_hit_penalty: int) -> list[str]:
        events: list[str] = []
        actor = s.actor
        prev_gid = bs.combatants[actor].group_id

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
            if s.target is None:
                raise ValueError("ATTACK requires target")
            r = basic_attack(bs, attacker=actor, defender=s.target, modifiers=IndexModifiers())
            events.append(f"STEP: ATTACK {actor}->{s.target} outcome={r['outcome']} dmg={r['damage']}")

        elif s.kind == "APPLY_EFFECT":
            if s.target is None:
                raise ValueError("APPLY_EFFECT requires target")
            if not s.effect_id or s.effect_duration is None:
                raise ValueError("APPLY_EFFECT requires effect_id/effect_duration")
            if s.status_inflict is None:
                raise ValueError("APPLY_EFFECT requires status_inflict")
            if s.effect_duration is None:
                raise ValueError("APPLY_EFFECT requires effect_duration (turns)")

            tgt = s.target
            eff = s.effect_id

            # ✅ 저항 지수는 엔진이 런타임에 계산
            resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)

            if not resist.resistible:
                # 정책: 저항 불가(즉사 등) => 부여 자동 성공
                bs.combatants[tgt].effects[eff] = int(s.effect_duration)
                events.append(
                    f"STATUS_CHECK: {actor}->{tgt} effect={eff} "
                    f"inflict={s.status_inflict} resist=NA resistible=False roll=NA success=True"
                )
                events.append(f"EFFECT_APPLIED: {tgt} +{eff}({s.effect_duration})")
            else:
                sr = roll_status_success(inflict=s.status_inflict, resist=resist.value)
                events.append(
                    f"STATUS_CHECK: {actor}->{tgt} effect={eff} "
                    f"inflict={s.status_inflict} resist={resist.value} resistible=True roll={sr.roll} success={sr.success}"
                )
                if sr.success:
                    dur_ticks = turns_to_ticks_for_battle(bs, int(s.effect_duration))
                    prev = bs.combatants[tgt].effects.get(eff, 0)
                    bs.combatants[tgt].effects[eff] = prev + dur_ticks
                    events.append(f"EFFECT_APPLIED: {tgt} +{eff}(turns={s.effect_duration}, ticks=+{dur_ticks}, total_ticks={prev + dur_ticks})")
                else:
                    events.append(f"EFFECT_RESISTED: {tgt} resisted {eff}")

        elif s.kind == "REMOVE_EFFECT":
            if s.target is None:
                raise ValueError("REMOVE_EFFECT requires target")
            if not s.effect_id:
                raise ValueError("REMOVE_EFFECT requires effect_id")

            tgt = s.target
            eff = s.effect_id

            if eff not in bs.combatants[tgt].effects:
                events.append(f"EFFECT_REMOVE_NOOP: {tgt} has_no {eff}")
                return events

            # ✅ 저항 지수는 엔진이 런타임에 계산
            resist = compute_status_resist_index(stats=bs.defs[tgt].stats, status_id=eff)

            if not resist.resistible:
                # 정책: 저항 불가(즉사 등) => 해제 불가(자동 실패)
                events.append(
                    f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                    f"inflict={DISPEL_INFLICT} resist=NA resistible=False roll=NA success=True"
                )
                events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
            else:
                sr = roll_status_success(inflict=DISPEL_INFLICT, resist=resist.value)

                # 부여와 정반대로 해석:
                # - sr.success=True  => '걸린다' 쪽이므로 상태 유지(해제 실패)
                # - sr.success=False => '안 걸린다' 쪽이므로 상태 해제(해제 성공)
                events.append(
                    f"DISPEL_CHECK: {actor}->{tgt} effect={eff} "
                    f"inflict={DISPEL_INFLICT} resist={resist.value} resistible=True roll={sr.roll} success={sr.success}"
                )
                if sr.success:
                    events.append(f"DISPEL_FAILED: {tgt} keeps {eff}")
                else:
                    del bs.combatants[tgt].effects[eff]
                    events.append(f"DISPEL_SUCCESS: {tgt} -{eff}")

        elif s.kind == "ATTACK_APPLY_EFFECT":
            if s.target is None:
                raise ValueError("ATTACK_APPLY_EFFECT requires target")
            if not s.effect_id or s.effect_duration is None:
                raise ValueError("ATTACK_APPLY_EFFECT requires effect_id/effect_duration")
            if s.status_inflict is None:
                raise ValueError("ATTACK_APPLY_EFFECT requires status_inflict")
            if s.effect_duration is None:
                raise ValueError("ATTACK_APPLY_EFFECT requires effect_duration (turns)")

            defender = s.target
            eff = s.effect_id

            r = basic_attack(bs, attacker=actor, defender=defender, modifiers=IndexModifiers())
            events.append(f"STEP: ATTACK {actor}->{defender} outcome={r['outcome']} dmg={r['damage']}")

            # 공격이 회피(EVADE)되면 상태이상 판정으로 가지 않음
            if r["outcome"] == "EVADE":
                events.append("STATUS_SKIPPED: attack evaded so no status check")
            else:
                resist = compute_status_resist_index(stats=bs.defs[defender].stats, status_id=eff)

                if not resist.resistible:
                    # 정책: 저항 불가 => 부여 자동 성공
                    bs.combatants[defender].effects[eff] = int(s.effect_duration)
                    events.append(
                        f"STATUS_CHECK: {actor}->{defender} effect={eff} "
                        f"inflict={s.status_inflict} resist=NA resistible=False roll=NA success=True"
                    )
                    events.append(f"EFFECT_APPLIED: {defender} +{eff}({s.effect_duration})")
                else:
                    sr = roll_status_success(inflict=s.status_inflict, resist=resist.value)
                    events.append(
                        f"STATUS_CHECK: {actor}->{defender} effect={eff} "
                        f"inflict={s.status_inflict} resist={resist.value} resistible=True roll={sr.roll} success={sr.success}"
                    )
                    if sr.success:
                        dur_ticks = turns_to_ticks_for_battle(bs, int(s.effect_duration))
                        prev = bs.combatants[defender].effects.get(eff, 0)
                        bs.combatants[defender].effects[eff] = prev + dur_ticks
                        events.append(f"EFFECT_APPLIED: {defender} +{eff}(turns={s.effect_duration}, ticks=+{dur_ticks}, total_ticks={prev + dur_ticks})")
                    else:
                        events.append(f"EFFECT_RESISTED: {defender} resisted {eff}")

        elif s.kind == "APPLY_MODIFIER":
            if s.target is None:
                raise ValueError("APPLY_MODIFIER requires target")
            if s.modifier_key is None or s.modifier_delta is None or s.modifier_duration is None:
                raise ValueError("APPLY_MODIFIER requires modifier_key/modifier_delta/modifier_duration")
            tgt = s.target

            # ✅ duration은 '턴' -> tick 변환
            dur_ticks = turns_to_ticks_for_battle(bs, int(s.modifier_duration))

            # ✅ 같은 key/delta라도 항상 새 인스턴스(중첩)
            mid = uuid.uuid4().hex
            key = s.modifier_key  # 문자열
            mi = ModifierInstance(
                mid=mid,
                key=key,  # type: ignore[arg-type]  (Literal 검사 목적; 런타임은 문자열)
                delta=int(s.modifier_delta),
                ticks_left=dur_ticks,
            )
            bs.combatants[tgt].modifiers.append(mi)
            events.append(
                f"MOD_APPLIED: {tgt} mid={mid} key={key} delta={mi.delta} turns={s.modifier_duration} ticks={dur_ticks}"
            )

        elif s.kind == "APPLY_HP_DELTA":
            if s.target is None:
                raise ValueError("APPLY_HP_DELTA requires target")
            if s.hp_delta is None:
                raise ValueError("APPLY_HP_DELTA requires hp_delta")
            tgt = s.target
            before = bs.combatants[tgt].hp
            bs.combatants[tgt].hp = before + int(s.hp_delta)  # hp setter가 0 clamp
            after = bs.combatants[tgt].hp
            events.append(f"HP_DELTA: {tgt} {before}->{after} (delta={int(s.hp_delta)})")

        else:
            raise ValueError(f"Unknown Step.kind: {s.kind}")
        
        # 쿨다운 저장
        self._maybe_register_cooldown(bs, actor=actor, s=s, events=events)

        return events

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

    # 쿨다운 체크
    def _assert_not_on_cooldown(self, bs: BattleState, *, actor: CombatantID, cooldown_id: str) -> None:
        cd = bs.combatants[actor].cooldowns.get(cooldown_id)
        if cd is not None and cd > 0:
            raise ValueError(f"Skill on cooldown: {cooldown_id} (ticks_left={cd})")

    # 쿨다운이 있는 스킬이면 쿨다운 저장
    def _maybe_register_cooldown(self, bs: BattleState, *, actor: CombatantID, s: Step, events: list[str]) -> None:
        """
        Step이 cooldown 정보를 가지면 (턴)->(tick) 환산해 actor.cooldowns에 저장
        """
        if not s.cooldown_id:
            return

        # ✅ 사용 전 쿨다운 체크
        self._assert_not_on_cooldown(bs, actor=actor, cooldown_id=s.cooldown_id)

        if s.cooldown_duration is None:
            return

        ticks = turns_to_ticks_for_battle(bs, int(s.cooldown_duration))
        bs.combatants[actor].cooldowns[s.cooldown_id] = ticks
        events.append(f"COOLDOWN_SET: {actor} {s.cooldown_id} turns={s.cooldown_duration} ticks={ticks}")
