from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal

from battle_system.core.types import CombatantID, GroupID, TeamID, AttackRange
# from battle_system.rules.indices.status import StatusTag

ModifierKey = Literal[
    "WEAK", "STRONG", "CRITICAL",
    "HIT", "EVADE",
    "STATUS_RESIST", "STATUS_INFLICT",
    "STR", "AGI", "CON", "INT", "WIS",
    "PHYSICAL_DEFENSE", "MAGIC_DEFENSE",
    "STEALTH_INFLICT", "STEALTH_RESIST",
    "ESCAPE_INFLICT", "ESCAPE_RESIST",
]

ItemWeight = Literal[0, 1, 2, 4, 8, 16]


@dataclass(frozen=True)
class Stats:
    str: int
    agi: int
    con: int
    int: int
    wis: int
    cha: int


@dataclass(frozen=True)
class CharacterDef:
    cid: CombatantID
    name: str
    level: int
    stats: Stats
    max_hp: int
    basic_attack_range: AttackRange = "MELEE"

@dataclass(frozen=True)
class ItemDef:
    item_id: str
    weight: ItemWeight


@dataclass
class ModifierInstance:
    """
    지속형 수치 수정 버프/디버프(modifier).
    - 같은 key/delta라도 항상 별도 인스턴스로 중첩(merge/연장 금지)
    """
    mid: int                 # 고유 id(엔진에서 발급)
    key: ModifierKey
    delta: int
    ticks_left: int
    status_tag: Optional[str] = None


@dataclass
class CombatantState:
    cid: CombatantID
    team: TeamID
    max_hp: int
    _hp: int
    group_id: GroupID

    can_main: bool = True
    can_sub: bool = True

    cooldowns: Dict[str, int] = field(default_factory=dict)
    effects: Dict[str, int] = field(default_factory=dict)
    modifiers: List[ModifierInstance] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        # 생성 시에도 클램프
        self._hp = max(0, int(self._hp))
    
    def _clamp_hp(self, v: int) -> int:
        v = int(v)
        if v < 0:
            return 0
        if v > self.max_hp:
            return self.max_hp
        return v

    @property
    def hp(self) -> int:
        return self._hp

    @hp.setter
    def hp(self, value: int) -> None:
        self._hp = self._clamp_hp(int(value))

    @property
    def is_down(self) -> bool:
        return self._hp <= 0


@dataclass
class BattleState:
    defs: Dict[CombatantID, CharacterDef]
    combatants: Dict[CombatantID, CombatantState]

    turn_order: List[CombatantID]
    turn_index: int = 0
    tick: int = 0

    groups: Dict[GroupID, List[CombatantID]] = field(default_factory=dict)

    items: Dict[str, ItemDef] = field(default_factory=dict)

    # 전투 시작 시점 인벤토리 스냅샷(전투 중 소모/획득은 여기서만 처리)
    inventory_snapshot: Dict[CombatantID, Dict[str, int]] = field(default_factory=dict)

    # 전투 종료 후 스토리 반영용 델타(투척이면 -1 기록)
    inventory_delta: Dict[CombatantID, Dict[str, int]] = field(default_factory=dict)

    xp_reward_total: int = 0

    ended: bool = False
    end_reason: Optional[str] = None

    def current_actor_id(self) -> CombatantID:
        return self.turn_order[self.turn_index]

    def current_actor(self) -> CombatantState:
        return self.combatants[self.current_actor_id()]

@dataclass(frozen=True)
class BattleDelta:
    """스토리에 커밋할 변화량(Phase 32 최소)."""
    hp_after: Dict[CombatantID, int]
    inventory_delta: Dict[CombatantID, Dict[str, int]]  # item_id -> +/- count


@dataclass(frozen=True)
class BattleResult:
    ended: bool
    end_reason: Optional[str]
    delta: BattleDelta
    events: List[str]  # 전투 로그(선택)