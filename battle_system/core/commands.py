from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal, List
from battle_system.core.types import CombatantID
from battle_system.rules.indices.crit import CritStat


StepKind = Literal[
    "MOVE_ENGAGE",
    "MOVE_DISENGAGE",
    "ATTACK",
    "APPLY_EFFECT",          # 판정 후 부여(공격 없이)
    "REMOVE_EFFECT",         # 정화/해제(판정 후 해제, 없으면 no-op)
    "APPLY_MODIFIER",        # 지속형 수치 수정(항상 성공, 중첩은 인스턴스 append)
    "APPLY_HP_DELTA",        # 현재 HP 즉시 변화(항상 성공, clamp)
]

ActionType = Literal["MAIN", "SUB"]


@dataclass(frozen=True)
class Step:
    """
    Step = 스킬 내부의 '미시 행동' 단위.
    - 쿨타임/행동슬롯은 Step이 아니라 Skill 단위에서 처리한다.
    - actor는 Skill.actor로 고정하고 Step에는 넣지 않는다.
    """
    kind: StepKind
    target: Optional[CombatantID] = None

    # 이동 관련
    reaction_immune: bool = False
    reaction_hit_penalty: int = 5  # move step에서만 의미 (engine에서 override 가능)

    # 상태이상/버프(effect) payload
    effect_id: Optional[str] = None
    effect_duration: Optional[int] = None   # ✅ "턴" 단위 (엔진이 tick으로 변환)
    status_inflict: Optional[int] = None    # ✅ 스킬이 제공하는 inflict(정수)

    # modifier(지속형 수치 수정) payload
    modifier_key: Optional[str] = None      # ModifierKey 문자열
    modifier_delta: Optional[int] = None    # 정수(± 가능)
    modifier_duration: Optional[int] = None # ✅ "턴" 단위 (엔진이 tick으로 변환)

    # hp delta(즉시 반영) payload
    hp_delta: Optional[int] = None          # 정수(± 가능), duration 없음

    require_prev_gte: int = 0


@dataclass(frozen=True)
class Skill:
    """
    Skill = 엔진이 실행하는 단위.
    - 행동 슬롯(MAIN/SUB) 소비
    - 쿨다운 확인/등록
    - steps 순차 실행
    """
    skill_id: str
    name: str
    actor: CombatantID
    action_type: ActionType = "MAIN"
    cooldown_turns: int = 0          # ✅ "턴" 단위 (엔진이 tick으로 변환)
    steps: List[Step] = None         # type: ignore[assignment]
    crit_stat: CritStat = "STR"
