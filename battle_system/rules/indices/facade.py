from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from battle_system.core.models import BattleState, Stats
from battle_system.core.types import CombatantID

from battle_system.rules.indices.hit import compute_hit_indices
from battle_system.rules.indices.crit import compute_crit_indices, CritStat


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

def _has_effect(bs: BattleState, cid: CombatantID, eff: str) -> bool:
    return bs.combatants[cid].effects.get(eff, 0) > 0

def _status_index_mods(
    bs: BattleState,
    attacker: CombatantID,
    defender: CombatantID,
    *,
    crit_stat: CritStat,
) -> tuple[IndexModifiers, IndexModifiers, int]:
    """
    상태이상이 공격/방어 지수에 주는 보정치를 반환한다.
    - 반환값:
      (attacker쪽 지수 보정, defender쪽 지수 보정, defender에게 적용될 weak_delta)
    """
    # IndexModifiers가 frozen이면 여기서 누적 후 마지막에 생성해야 함
    atk_hit = atk_weak = atk_strong = atk_crit = 0
    def_evade = 0
    def_weak_delta = 0

    # ---- attacker에 걸린 상태이상: HIT/CRIT 가중치 변화 ----
    if _has_effect(bs, attacker, "Confusion"):
        atk_hit += -20
        # Confusion은 회피 -5인데 "자기 회피"이므로 defender가 아니라 attacker에 걸린 경우 evade에 반영되어야 함
        # -> evade는 defender의 스탯으로만 계산되므로, attacker 회피 패널티는 'defender가 attacker일 때'만 의미가 있음.
        # 여기서는 공격 중인 attacker의 evade는 쓰이지 않으니, Confusion의 evade -5는
        # "피격 시 회피 지수 -5"로 해석하여 defender측에 있을 때만 반영하는 게 일관적이다.
        # (원하면 별도 방어 계산 entry에서 attacker의 evade 패널티도 처리 가능)
    if _has_effect(bs, attacker, "Fear"):
        atk_hit += -10
    if _has_effect(bs, attacker, "Blind"):
        atk_hit += -40
    if _has_effect(bs, attacker, "Slow"):
        atk_hit += -10

    # Weakness: "자신의 물리 공격에 대한 약공 지수 +20"
    if _has_effect(bs, attacker, "Weakness") and crit_stat in ("STR", "AGI"):
        atk_weak += 20

    # ---- defender에 걸린 상태이상: EVADE 변화 ----
    if _has_effect(bs, defender, "Confusion"):
        def_evade += -5
    if _has_effect(bs, defender, "Fear"):
        def_evade += -50
    if _has_effect(bs, defender, "Slow"):
        def_evade += -10
    if _has_effect(bs, defender, "Bind"):
        def_evade += -50

    # ---- defender에 걸린 상태이상: "약공 피격 지수" 변화 -> defender weak 가중치에 직접 가산 ----
    # (표의 -10/-15는 그대로 weak 가중치에 더한다)
    if _has_effect(bs, defender, "Burned"):
        def_weak_delta += -10
    if _has_effect(bs, defender, "Frostbite"):
        def_weak_delta += -10
    if _has_effect(bs, defender, "Frozen"):
        def_weak_delta += -15

    # Stun: 물리 약공 피격 -5
    if _has_effect(bs, defender, "Stun") and crit_stat in ("STR", "AGI"):
        def_weak_delta += -5

    # Paralysis: 마법 약공 피격 -5
    if _has_effect(bs, defender, "Paralysis") and crit_stat in ("INT", "WIS"):
        def_weak_delta += -5

    # Corruption: 마법 약공 피격 -15
    if _has_effect(bs, defender, "Corruption") and crit_stat in ("INT", "WIS"):
        def_weak_delta += -15

    atk_extra = IndexModifiers(hit=atk_hit, evade=0, weak=atk_weak, strong=atk_strong, critical=atk_crit)
    def_extra = IndexModifiers(hit=0, evade=def_evade, weak=0, strong=0, critical=0)
    return atk_extra, def_extra, def_weak_delta


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

    # 4) 지수 변환 상태이상 반영
    atk_eff, def_eff, def_weak_delta = _status_index_mods(
        bs, attacker, defender, crit_stat=crit_stat
    )

    base_he = HitEvasionIndices(
        hit=base_he.hit + atk_eff.hit,
        evade=base_he.evade + def_eff.evade,
    )
    base_crit = CritIndices(
        weak=base_crit.weak + atk_eff.weak + def_weak_delta,
        strong=base_crit.strong + atk_eff.strong,
        critical=base_crit.critical + atk_eff.critical,
    )

    # 5) 스킬/상황 modifiers 가산 
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
