from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from battle_system.core.models import BattleState, Stats
from battle_system.core.types import CombatantID

from battle_system.rules.indices.hit import compute_hit_indices
from battle_system.rules.indices.crit import compute_crit_indices, CritStat


# ==============================
# ⚠ 밸런스 조절(공식)은 하위 모듈에서만 ⚠
#   - hit.py / crit.py / status.py
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

def _effective_stats(bs: BattleState, cid: CombatantID) -> Stats:
    """
    캐릭터의 base stats + (CombatantState.modifiers 중 STR/AGI/CON/INT/WIS 합산)을 반영한 유효 스탯.
    - Stats 자체를 BattleState에 영구적으로 덮어쓰지 않는다(계산 시점에만 사용).
    - 0 미만은 0으로 클램프.
    """
    base = bs.defs[cid].stats
    st = bs.combatants[cid]

    d_str = d_agi = d_con = d_int = d_wis = 0
    for m in st.modifiers:
        if m.key == "STR":
            d_str += int(m.delta)
        elif m.key == "AGI":
            d_agi += int(m.delta)
        elif m.key == "CON":
            d_con += int(m.delta)
        elif m.key == "INT":
            d_int += int(m.delta)
        elif m.key == "WIS":
            d_wis += int(m.delta)

    return Stats(
        str=max(0, base.str + d_str),
        agi=max(0, base.agi + d_agi),
        con=max(0, base.con + d_con),
        int=max(0, base.int + d_int),
        wis=max(0, base.wis + d_wis),
        cha=base.cha,
    )


def _defense_to_weak_delta(bs: BattleState, defender: CombatantID, *, crit_stat: CritStat) -> int:
    """
    방어 modifier의 효과:
    - PHYSICAL_DEFENSE / MAGIC_DEFENSE의 delta만큼 defender가 맞을 때 WEAK 가중치(지수)를 올린다.
    - crit_stat이 STR/AGI면 물리, INT/WIS면 마법으로 판정.
    """
    st = bs.combatants[defender]
    is_physical = crit_stat in ("STR", "AGI")
    is_magic = crit_stat in ("INT", "WIS")

    delta = 0
    for m in st.modifiers:
        if m.key == "PHYSICAL_DEFENSE" and is_physical:
            delta += int(m.delta)
        elif m.key == "MAGIC_DEFENSE" and is_magic:
            delta += int(m.delta)
    return delta

def _sum_index_mods(bs: BattleState, cid: CombatantID) -> IndexModifiers:
    """
    CombatantState.modifiers 중 '지수에 직접 더하는' 타입을 합산해서 반환.
    IndexModifiers가 frozen이므로, 지역 변수로 누적 후 최종 생성한다.
    """
    st = bs.combatants[cid]

    hit = evade = weak = strong = critical = 0

    for m in st.modifiers:
        d = int(m.delta)
        if m.key == "WEAK":
            weak += d
        elif m.key == "STRONG":
            strong += d
        elif m.key == "CRITICAL":
            critical += d
        elif m.key == "HIT":
            hit += d
        elif m.key == "EVADE":
            evade += d

    return IndexModifiers(hit=hit, evade=evade, weak=weak, strong=strong, critical=critical)

def compute_base_hit_evasion(
    bs: BattleState,
    attacker: CombatantID,
    defender: CombatantID,
) -> HitEvasionIndices:
    """
    [기본] 명중/회피 지수 계산.
    스탯 변환 계산 수행.

    실제 공식은 rules/indices/hit.py 에 있다.
    """
    atk = bs.defs[attacker]
    def_stats = _effective_stats(bs, defender)

    he = compute_hit_indices(attacker_level=atk.level, defender_stats=def_stats)
    return HitEvasionIndices(hit=he.hit, evade=he.evade)


def compute_base_crit(
    bs: BattleState,
    attacker: CombatantID,
    defender: CombatantID,
    *,
    crit_stat: CritStat,
) -> CritIndices:
    """
    [기본] 약공/강공/치명타 지수 계산.
    스탯 변환 계산 수행.

    실제 공식은 rules/indices/crit.py 에 있다.
    """
    atk = bs.defs[attacker]

    atk_stats = _effective_stats(bs, attacker)
    def_stats = _effective_stats(bs, defender)

    ci = compute_crit_indices(attacker_level=atk.level, attacker_stats=atk_stats, defender_stats=def_stats, crit_stat=crit_stat)
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

    변경점(Phase 25):
      - defender의 PHYSICAL_DEFENSE/MAGIC_DEFENSE는
        피격 시 WEAK 지수(가중치)를 올리는 방식으로 반영한다.
      - IndexModifiers는 스킬/상황 보정치로만 사용한다.
    """
    # 1) base 지수 계산
    base_he = compute_base_hit_evasion(bs, attacker, defender)
    base_crit = compute_base_crit(bs, attacker, defender, crit_stat=crit_stat)

    # 2) 방어 modifier(피격 시 weak 증가) 반영: base_crit에 선적용
    weak_def_delta = _defense_to_weak_delta(bs, defender, crit_stat=crit_stat)
    base_crit = CritIndices(
        weak=base_crit.weak + weak_def_delta,
        strong=base_crit.strong,
        critical=base_crit.critical,
    )

    # 3) 캐릭터 지속 modifier(지수 직접 보정) 반영
    atk_mods = _sum_index_mods(bs, attacker)
    def_mods = _sum_index_mods(bs, defender)

    base_he = HitEvasionIndices(
        hit=base_he.hit + atk_mods.hit,
        evade=base_he.evade + def_mods.evade,
    )
    base_crit = CritIndices(
        weak=base_crit.weak + atk_mods.weak,
        strong=base_crit.strong + atk_mods.strong,
        critical=base_crit.critical + atk_mods.critical,
    )

    # 4) 스킬/상황 modifiers 가산 
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
