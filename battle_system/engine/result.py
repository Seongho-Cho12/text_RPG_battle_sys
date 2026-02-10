from __future__ import annotations

from copy import deepcopy
from typing import Dict

from battle_system.core.models import BattleState, BattleDelta, BattleResult


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

def build_battle_result(bs: BattleState, events: list[str]) -> BattleResult:
    delta = extract_battle_delta(bs)
    return BattleResult(
        ended=bool(bs.ended),
        end_reason=getattr(bs, "end_reason", None),
        delta=delta,
        events=list(events),
    )