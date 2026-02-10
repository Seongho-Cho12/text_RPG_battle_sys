from __future__ import annotations

from copy import deepcopy
from typing import Dict, List
import random

from battle_system.core.models import BattleState, BattleDelta, BattleResult
from battle_system.engine.rewards import compute_victory_rewards


def extract_battle_delta(bs: BattleState) -> BattleDelta:
    """
    BattleState로부터 '스토리 반영용' 변화량을 뽑는다.
    - 전투가 끝난 뒤에만 호출해야 한다.
    - Phase 32에서는 HP / inventory_delta만 포함한다.
    """
    if not getattr(bs, "ended", False):
        raise ValueError("extract_battle_delta() called before battle ended")

    hp_after: Dict = {}
    for cid, st in bs.combatants.items():
        # 프로젝트 코드에서 hp 접근 방식에 맞춰 하나로 통일하면 됨.
        # (대부분 st.hp 프로퍼티가 있을 가능성이 큼)
        hp_after[cid] = int(getattr(st, "hp", getattr(st, "_hp")))

    inv_delta = deepcopy(getattr(bs, "inventory_delta", {}))
    return BattleDelta(hp_after=hp_after, inventory_delta=inv_delta)

def build_battle_result(
    bs: BattleState,
    events: List[str],
    *,
    rng: random.Random | None = None,
) -> BattleResult:
    """
    전투 종료 후 1회 호출.
    - delta 추출
    - 승리면 보상 계산 후 inventory_delta에 합산(+)
    - xp_each_ally/reward_events 채움
    """
    delta = extract_battle_delta(bs)

    rewards = compute_victory_rewards(bs, rng=rng)

    # inventory_delta 합산(투척 -1 등 기존 delta + 드랍 +)
    merged_inv = deepcopy(delta.inventory_delta)
    for cid, d in rewards.inventory_delta.items():
        base = merged_inv.setdefault(cid, {})
        for item_id, add in d.items():
            base[item_id] = base.get(item_id, 0) + add
            if base[item_id] == 0:
                del base[item_id]

    delta = BattleDelta(hp_after=delta.hp_after, inventory_delta=merged_inv)

    return BattleResult(
        ended=bool(getattr(bs, "ended", False)),
        end_reason=getattr(bs, "end_reason", None),
        delta=delta,
        events=list(events),
        xp_each_ally=rewards.xp_each_ally,
        reward_events=rewards.events,
    )