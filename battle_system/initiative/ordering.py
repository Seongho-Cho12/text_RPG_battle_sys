from __future__ import annotations
from typing import Dict, List

from battle_system.core.types import CombatantID
from battle_system.core.models import CharacterDef


def compute_turn_order(defs: Dict[CombatantID, CharacterDef]) -> List[CombatantID]:
    """
    선공권: AGI desc -> WIS desc -> LEVEL desc
    (완전 동률일 때는 cid로 안정적인 정렬)
    """
    def key(cid: CombatantID):
        d = defs[cid]
        return (-d.stats.agi, -d.stats.wis, -d.level, str(d.cid))

    return sorted(defs.keys(), key=key)
