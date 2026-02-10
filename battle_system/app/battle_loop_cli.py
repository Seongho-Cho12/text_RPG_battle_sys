# battle_system/app/battle_loop_cli.py
from __future__ import annotations

from typing import Dict, List, Optional

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState
from battle_system.core.commands import Skill
from battle_system.engine.engine import BattleEngine

from battle_system.app.skill_ui import (
    list_usable_skills,
    get_skill_availability,
    instantiate_skill_with_inputs,
)


def run_battle_cli(
    bs: BattleState,
    *,
    engine: BattleEngine,
    skills_by_actor: Dict[CombatantID, List[Skill]],
) -> BattleState:
    while not bs.ended:
        actor = bs.current_actor_id()
        st = bs.combatants[actor]

        print("\n" + "=" * 80)
        print(f"TICK={bs.tick} ACTOR={actor} HP={st.hp}/{st.max_hp} can_main={st.can_main} can_sub={st.can_sub}")
        if st.cooldowns:
            print(f"COOLDOWNS: {dict(st.cooldowns)}")
        if st.effects:
            print(f"EFFECTS: {dict(st.effects)}")

        # DOWN이면 자동 턴 종료
        if st.is_down:
            print("AUTO: actor DOWN -> end_turn")
            bs = engine.end_turn(bs)
            continue

        my_skills = skills_by_actor.get(actor, [])

        usable_main = list_usable_skills(bs, actor=actor, skills=my_skills, action_type="MAIN")
        usable_sub = list_usable_skills(bs, actor=actor, skills=my_skills, action_type="SUB")

        no_main = (not st.can_main) or (st.can_main and len(usable_main) == 0)
        no_sub = (not st.can_sub) or (st.can_sub and len(usable_sub) == 0)

        # 남은 슬롯이 있어도 그 슬롯에 대해 사용 가능한 스킬이 없으면 자동 턴 종료
        if no_main and no_sub:
            print("AUTO: no usable skills for remaining slots -> end_turn")
            bs = engine.end_turn(bs)
            continue

        # 선택지 출력
        print("\n[Choose]")
        idx_to_skill: List[Skill] = []

        if st.can_main and usable_main:
            print(" MAIN:")
            for sk, _av in usable_main:
                idx_to_skill.append(sk)
                print(f"  [{len(idx_to_skill)-1}] {sk.skill_id} ({sk.name})")

        if st.can_sub and usable_sub:
            print(" SUB:")
            for sk, _av in usable_sub:
                idx_to_skill.append(sk)
                print(f"  [{len(idx_to_skill)-1}] {sk.skill_id} ({sk.name})")

        print("  [T] turn pass")

        sel = input("Select: ").strip()
        if sel.lower() == "t":
            bs = engine.end_turn(bs)
            continue

        try:
            k = int(sel)
            chosen = idx_to_skill[k]
        except Exception:
            print("Invalid selection")
            continue

        av = get_skill_availability(bs, chosen)
        if not av.usable:
            # 엔진 메시지가 있으면 그대로 표시
            if av.engine_message:
                print(f"NOT USABLE: {av.reason} / {av.engine_message}")
            else:
                print(f"NOT USABLE: {av.reason}")
            continue

        target: Optional[CombatantID] = None
        throw_item_id: Optional[str] = None

        if av.spec.target_required:
            print("\n[Targets]")
            for i, cid in enumerate(av.spec.target_candidates):
                cst = bs.combatants[cid]
                print(f"  [{i}] {cid} team={cst.team} hp={cst.hp}/{cst.max_hp} gid={cst.group_id}")
            s2 = input("Choose target: ").strip()
            try:
                ti = int(s2)
                target = av.spec.target_candidates[ti]
            except Exception:
                print("Invalid target")
                continue

        if av.spec.item_required:
            print("\n[Items]")
            snap = bs.inventory_snapshot.get(actor, {})
            for i, item_id in enumerate(av.spec.item_candidates):
                print(f"  [{i}] {item_id} x{snap.get(item_id, 0)}")
            s3 = input("Choose item: ").strip()
            try:
                ii = int(s3)
                throw_item_id = av.spec.item_candidates[ii]
            except Exception:
                print("Invalid item")
                continue

        concrete = instantiate_skill_with_inputs(chosen, target=target, throw_item_id=throw_item_id)
        bs2 = engine.apply_skill(bs, concrete)

        # 이벤트 출력
        for e in bs2.events:
            print("  " + e)

        # 같은 actor가 남은 슬롯이 있으면 계속 진행(루프 상단으로)
        bs = bs2

    print("\n" + "=" * 80)
    print(f"BATTLE ENDED: reason={bs.end_reason}")
    return bs
