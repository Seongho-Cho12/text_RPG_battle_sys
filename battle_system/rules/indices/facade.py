from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from battle_system.core.models import BattleState
from battle_system.core.types import CombatantID

from battle_system.rules.indices.hit import compute_hit_indices
from battle_system.rules.indices.crit import compute_crit_indices, CritStat


# ==============================
# ⚠ 밸런스 조절(공식)은 하위 모듈에서만 ⚠
#   - hit.py / crit.py / (추후 status.py)
#   facade는 "통합 진입점"만 제공한다.
# ==============================


@dataclass(frozen=True)
class HitEvasionIndices:
    hit: int
    evade: int


@dataclass(frozen=True)
class CritIndices:
    weak: int
    strong: int
    critical: int


@dataclass(frozen=True)
class AttackIndices:
    hit_eva: HitEvasionIndices
    crit: CritIndices


@dataclass(frozen=True)
class IndexModifiers:
    """
    공격/스킬이 추가로 주는 보정치(가산형).
    - 기본 공격은 modifiers=default(전부 0)
    - 스킬은 여기 값을 채워서 넘기면 된다.
    """
    hit: int = 0
    evade: int = 0
    weak: int = 0
    strong: int = 0
    critical: int = 0


def _apply_mod(v: int, dv: int) -> int:
    return max(0, int(v + dv))


def compute_base_hit_evasion(
    bs: BattleState,
    attacker: CombatantID,
    defender: CombatantID,
) -> HitEvasionIndices:
    """
    [기본] 명중/회피 지수 계산.

    실제 공식은 rules/indices/hit.py 에 있다.
    """
    atk = bs.defs[attacker]
    dfn = bs.defs[defender]

    he = compute_hit_indices(attacker_level=atk.level, defender_stats=dfn.stats)
    return HitEvasionIndices(hit=he.hit, evade=he.evade)


def compute_base_crit(
    bs: BattleState,
    attacker: CombatantID,
    *,
    crit_stat: CritStat,
) -> CritIndices:
    """
    [기본] 약공/강공/치명타 지수 계산.

    실제 공식은 rules/indices/crit.py 에 있다.
    """
    atk = bs.defs[attacker]
    ci = compute_crit_indices(attacker_level=atk.level, attacker_stats=atk.stats, crit_stat=crit_stat)
    return CritIndices(weak=ci.weak, strong=ci.strong, critical=ci.crit)


def compute_attack_indices(
    bs: BattleState,
    attacker: CombatantID,
    defender: CombatantID,
    *,
    crit_stat: CritStat = "STR",
    modifiers: IndexModifiers = IndexModifiers(),
) -> AttackIndices:
    """
    공격(기본/스킬/반응) 공통 지수 계산 Entry Point.

    흐름:
      1) base 지수 계산 (hit.py / crit.py)
      2) modifiers(스킬/상황) 가산
      3) 최종 지수 반환
    """
    base_he = compute_base_hit_evasion(bs, attacker, defender)
    base_crit = compute_base_crit(bs, attacker, crit_stat=crit_stat)

    he = HitEvasionIndices(
        hit=_apply_mod(base_he.hit, modifiers.hit),
        evade=_apply_mod(base_he.evade, modifiers.evade),
    )
    crit = CritIndices(
        weak=_apply_mod(base_crit.weak, modifiers.weak),
        strong=_apply_mod(base_crit.strong, modifiers.strong),
        critical=_apply_mod(base_crit.critical, modifiers.critical),
    )
    return AttackIndices(hit_eva=he, crit=crit)
