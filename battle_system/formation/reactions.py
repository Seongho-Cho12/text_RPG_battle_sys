from __future__ import annotations
from typing import List, Iterable

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState


def reaction_attack_candidates(
    bs: BattleState,
    mover: CombatantID,
    *,
    prev_group_id,
    reaction_immune: bool,
) -> List[CombatantID]:
    """
    이동에 따른 반응공격(기회공격) 후보를 산출한다.

    규칙:
      - reaction_immune == True 이면 빈 리스트
      - 이동 '직전' 그룹(prev_group_id)에 있던 적들만 고려
      - mover 자신은 제외
      - 기본 공격이 근접(MELEE)인 적만 후보
      - 같은 팀은 제외 (적만)

    반환:
      - 반응공격을 수행할 CombatantID 리스트 (순서는 그룹 내 순서 유지)
    """
    if reaction_immune:
        return []

    if mover not in bs.combatants:
        raise ValueError("mover must exist in battle")

    mover_state = bs.combatants[mover]
    mover_def = bs.defs[mover]

    candidates: List[CombatantID] = []

    members = bs.groups.get(prev_group_id, [])
    for cid in members:
        if cid == mover:
            continue

        st = bs.combatants[cid]
        d = bs.defs[cid]

        # 전투불능 객체 제외
        if st.is_down:
            continue

        # 같은 팀 제외
        if st.team == mover_state.team:
            continue

        # 근접 공격만 반응공격 가능
        if d.basic_attack_range != "MELEE":
            continue

        candidates.append(cid)

    return candidates
