from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import random

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState
from battle_system.rules.checks import roll_status_success


@dataclass(frozen=True)
class VictoryRewards:
    xp_each_ally: int
    inventory_delta: Dict[CombatantID, Dict[str, int]]  # 아군별 +획득
    events: List[str]


def compute_victory_rewards(
    bs: BattleState,
    *,
    rng: Optional[random.Random] = None,
) -> VictoryRewards:
    """
    - ALLY_VICTORY일 때만 지급
    - XP: sum( enemy_level^2 * 10 ) for downed enemies
      -> 아군 전원 동일 지급 (xp_each_ally)
    - 드랍: 각 몬스터 drops에 대해, 각 아군마다 독립 확률 판정으로 획득
      (상태이상 굴리기처럼 inflict=p, resist=100-p)
    """
    rng = rng or random.Random()
    events: List[str] = []

    if (not bs.ended) or (getattr(bs, "end_reason", None) != "ALLY_VICTORY"):
        return VictoryRewards(xp_each_ally=0, inventory_delta={}, events=["REWARD: skipped (not ally victory)"])

    allies = [cid for cid, st in bs.combatants.items() if st.team == "ALLY"]
    enemies = [cid for cid, st in bs.combatants.items() if st.team == "ENEMY"]

    # XP
    total = 0
    for eid in enemies:
        if bs.combatants[eid].is_down:
            lv = int(bs.defs[eid].level)
            total += (lv * lv) * 10
    events.append(f"REWARD_XP_EACH_ALLY={total}")

    # LOOT
    loot: Dict[CombatantID, Dict[str, int]] = {a: {} for a in allies}

    for eid in enemies:
        if not bs.combatants[eid].is_down:
            continue

        drops = getattr(bs.defs[eid], "drops", [])
        for d in drops:
            p = int(d.chance_percent)
            if not (0 <= p <= 100):
                raise ValueError(f"Invalid drop chance {p} for {d.item_id} (enemy={eid})")

            for aid in allies:
                sr = roll_status_success(inflict=p, resist=100 - p, rng=rng)
                if sr.success:
                    inv = loot[aid]
                    inv[d.item_id] = inv.get(d.item_id, 0) + 1
                    events.append(f"REWARD_LOOT ally={aid} item={d.item_id} from={eid} p={p} roll={sr.roll}")

    return VictoryRewards(xp_each_ally=total, inventory_delta=loot, events=events)
