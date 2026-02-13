# battle_system/app/battle_runner.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState, CharacterDef, ItemDef
from battle_system.core.commands import Skill
from battle_system.engine.engine import BattleEngine

from battle_system.app.battle_loop_cli import run_battle_cli
from battle_system.app.post_battle import build_and_apply_battle_outcome


@dataclass(frozen=True)
class BattleRunResult:
    battle_state: BattleState
    # 전투 결과 반영 요약(메타에 적용된 값)
    hp_after: Dict[CombatantID, int]
    inventory_delta: Dict[CombatantID, Dict[str, int]]
    xp_each_ally: int
    end_reason: str


def run_battle(
    *,
    engine: BattleEngine,
    allies: List[CharacterDef],
    enemies: List[CharacterDef],
    skills_by_actor: Dict[CombatantID, List[Skill]],
    initial_inventory: Optional[Dict[CombatantID, Dict[str, int]]] = None,
    items: Optional[Dict[str, ItemDef]] = None,
) -> BattleRunResult:
    """
    전투 생성 -> 턴 루프 -> 종료 -> 결과 생성/반영 까지 한 번에 수행.
    """
    bs = engine.create_battle(allies=allies, enemies=enemies)

    if items is not None:
        bs.items = dict(items)
    
    # 초기 인벤토리(없으면 엔진 기본값/빈 값)
    if initial_inventory is not None:
        for cid, inv in initial_inventory.items():
            if cid in bs.inventory_snapshot:
                bs.inventory_snapshot[cid].update(inv)
            else:
                bs.inventory_snapshot[cid] = dict(inv)

    bs = run_battle_cli(bs, engine=engine, skills_by_actor=skills_by_actor)

    # 종료 후 결과 반영(HP/인벤/XP)
    outcome = build_and_apply_battle_outcome(engine=engine, bs=bs)

    return BattleRunResult(
        battle_state=outcome.battle_state,
        hp_after=outcome.hp_after,
        inventory_delta=outcome.inventory_delta,
        xp_each_ally=outcome.xp_each_ally,
        end_reason=outcome.end_reason,
    )
