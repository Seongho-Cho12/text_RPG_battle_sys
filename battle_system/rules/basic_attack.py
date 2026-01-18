from __future__ import annotations
from typing import Dict, List

from battle_system.core.types import CombatantID
from battle_system.core.models import BattleState
from battle_system.rules.checks import hit_check, crit_check
from battle_system.rules.indices.facade import compute_attack_indices, IndexModifiers
from battle_system.rules.indices.crit import CritStat


DAMAGE_TABLE = {
    "WEAK": 1,
    "STRONG": 3,
    "CRITICAL": 9,
}


def basic_attack(
    bs: BattleState,
    attacker: CombatantID,
    defender: CombatantID,
    *,
    modifiers: IndexModifiers = IndexModifiers(),  # 기본 공격은 기본값(0)
    crit_stat: CritStat = "STR",
) -> dict:
    """
    기본 공격:
      - 지수 계산: compute_attack_indices(...)
      - 판정: hit_check, crit_check
      - 데미지 적용

    스킬 공격도 같은 루트를 쓰되 modifiers만 다르게 주면 된다.
    """
    indices = compute_attack_indices(bs, attacker, defender, modifiers=modifiers, crit_stat=crit_stat)

    hit = hit_check(
        hit_index=indices.hit_eva.hit,
        evade_index=indices.hit_eva.evade,
    )
    if hit.outcome == "EVADE":
        return {"hit": False, "outcome": "EVADE", "damage": 0}

    crit = crit_check(
        weak_index=indices.crit.weak,
        strong_index=indices.crit.strong,
        crit_index=indices.crit.critical,
    )

    dmg = DAMAGE_TABLE[crit.outcome]
    bs.combatants[defender].hp -= dmg

    return {"hit": True, "outcome": crit.outcome, "damage": dmg}


def execute_reaction_attacks(
    bs: BattleState,
    mover: CombatantID,
    candidates: List[CombatantID],
    *,
    reaction_hit_penalty: int,
) -> Dict[CombatantID, dict]:
    """
    반응공격:
      - 기본 공격과 동일한 루트
      - 단, 명중 지수(hit)에 페널티를 주기 위해 modifiers.hit에 -penalty 적용
        (즉, hit를 낮추는 방향)
    """
    results: Dict[CombatantID, dict] = {}
    for attacker in candidates:
        results[attacker] = basic_attack(
            bs,
            attacker=attacker,
            defender=mover,
            modifiers=IndexModifiers(hit=-reaction_hit_penalty),
        )
    return results
