from __future__ import annotations
from typing import List

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState


def same_group(bs: BattleState, a: CombatantID, b: CombatantID) -> bool:
    return bs.combatants[a].group_id == bs.combatants[b].group_id


def can_melee(bs: BattleState, attacker: CombatantID, target: CombatantID) -> bool:
    # 근접 공격: 같은 그룹만 가능
    return same_group(bs, attacker, target)


def can_ranged(bs: BattleState, attacker: CombatantID, target: CombatantID) -> bool:
    # 원거리 공격: 다른 그룹만 가능
    return not same_group(bs, attacker, target)


def members_of_group(bs: BattleState, gid) -> List[CombatantID]:
    return list(bs.groups.get(gid, []))
