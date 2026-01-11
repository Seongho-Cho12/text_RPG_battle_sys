from dataclasses import dataclass
from typing import Optional, Literal
from battle_system.core.types import CombatantID

StepKind = Literal[
    "MOVE_ENGAGE",
    "MOVE_DISENGAGE",
    "ATTACK",
    "APPLY_EFFECT",          # 판정 후 부여(공격 없이)
    "REMOVE_EFFECT",         # 정화/해제(존재하면 제거, 없으면 no-op)
    "ATTACK_APPLY_EFFECT",   # 공격 성공(명중) 시 판정 후 부여
    "APPLY_MODIFIER",
    "APPLY_HP_DELTA",
]
ActionType = Literal["MAIN", "SUB"]

@dataclass(frozen=True)
class Step:
    kind: StepKind
    actor: CombatantID
    target: Optional[CombatantID] = None

    # 이동 관련
    reaction_immune: bool = False
    reaction_hit_penalty: int = 5  # move step에서만 의미

    # 행동 슬롯
    action_type: ActionType = "MAIN"

    # 상태이상/버프 payload (Phase 14)
    effect_id: Optional[str] = None
    effect_duration: Optional[int] = None
    status_inflict: Optional[int] = None

    # ----- cooldown -----
    cooldown_id: Optional[str] = None
    cooldown_duration: Optional[int] = None  # ✅ "턴" 단위 쿨타임

    # ----- modifier(지속형 수치 수정) -----
    modifier_key: Optional[str] = None         # ModifierKey 문자열
    modifier_delta: Optional[int] = None       # 정수(± 가능)
    modifier_duration: Optional[int] = None    # ✅ "턴" 단위 (엔진이 tick으로 변환)

    # ----- hp delta(즉시 반영) -----
    hp_delta: Optional[int] = None             # 정수(± 가능), duration 없음


@dataclass(frozen=True)
class Skill:
    """
    스킬 단위 컨테이너.
    - 슬롯(MAIN/SUB) + 쿨타임은 스킬 단위로 처리한다.
    - 실제 실행은 steps를 순차 수행한다.
    """
    skill_id: str
    name: str
    actor: CombatantID
    action_type: ActionType = "MAIN"

    # 쿨타임은 '턴' 단위로 저장(엔진이 tick으로 변환)
    cooldown_turns: int = 0

    steps: list[Step] = None  # type: ignore[assignment]