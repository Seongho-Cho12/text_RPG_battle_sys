# battle_system/app/post_battle.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState
from battle_system.engine.engine import BattleEngine
from battle_system.engine.rewards import compute_rewards  # 너 코드에 맞는 함수명 사용
from battle_system.engine.result import extract_battle_delta  # 너 코드에 맞는 함수명 사용


@dataclass(frozen=True)
class BattleOutcome:
    battle_state: BattleState
    hp_after: Dict[CombatantID, int]
    inventory_delta: Dict[CombatantID, Dict[str, int]]
    xp_each_ally: int
    end_reason: str


def build_and_apply_battle_outcome(*, engine: BattleEngine, bs: BattleState) -> BattleOutcome:
    """
    전투 종료 후,
    - hp_after / inventory_delta / xp를 산출하고
    - (선택) 외부 메타 저장소에 적용할 수 있도록 반환
    """
    if not bs.ended:
        raise ValueError("battle is not ended")

    # delta는 hp_after + inventory_delta 형태로 나오는 게 Phase33 기준
    delta = extract_battle_delta(bs)

    # rewards는 ALLY_VICTORY에서만 xp/drop 발생하는 게 Phase33 기준
    rewards = compute_rewards(bs)

    # hp_after
    hp_after = dict(delta.hp_after)

    # inventory_delta
    inventory_delta = {cid: dict(d) for cid, d in delta.inventory_delta.items()}

    # xp_each_ally (없으면 0)
    xp_each_ally = int(getattr(rewards, "xp_each_ally", 0))

    # 여기서 “적용”은 프로젝트 메타 레이어가 따로 있다면 그쪽에서 하는 게 맞음.
    # Phase34에서는 "반영값 계산"까지만 확정.

    return BattleOutcome(
        battle_state=bs,
        hp_after=hp_after,
        inventory_delta=inventory_delta,
        xp_each_ally=xp_each_ally,
        end_reason=str(bs.end_reason),
    )
