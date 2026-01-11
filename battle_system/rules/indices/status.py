from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal

from battle_system.core.models import Stats


# ------------------------------
# 상태이상 ID 정의
# ------------------------------
StatusID = Literal[
    "BLEEDING",
    "POISONED",
    "BURNED",
    "FROSTBITE",
    "STUN",
    "CONFUSION",
    "FEAR",
    "CORRUPTION",
    "CURSE",
    "WEAKNESS",
    "DECAY",
    "BIND",
    "BLIND",
    "SLOW",
    "PARALYSIS",
    "INSTANT_DEATH",
    "FROZEN",
    "OBLIVION",
]

ResistStat = Optional[Literal["STR", "AGI", "INT", "WIS"]]


# ------------------------------
# 상태이상 → 저항 보조 스탯 매핑
# ------------------------------
STATUS_RESIST_STAT: dict[StatusID, ResistStat] = {
    "BLEEDING": "STR",
    "POISONED": None,
    "BURNED": None,
    "FROSTBITE": None,
    "STUN": "STR",
    "CONFUSION": "WIS",
    "FEAR": "INT",
    "CORRUPTION": None,
    "CURSE": "INT",
    "WEAKNESS": "STR",
    "DECAY": "WIS",
    "BIND": "STR",
    "BLIND": "INT",
    "SLOW": "AGI",
    "PARALYSIS": "STR",
    "INSTANT_DEATH": None,  # 특수 처리
    "FROZEN": None,
    "OBLIVION": "INT",
}


@dataclass(frozen=True)
class StatusResistIndex:
    """
    상태이상 저항 지수 결과.
    - value: 저항 지수
    - resistible: False 인 경우 판정 자체를 하지 않음 (즉사 등)
    """
    value: int
    resistible: bool


# ------------------------------
# 핵심 계산 함수
# ------------------------------
def compute_status_resist_index(
    *,
    stats: Stats,
    status_id: StatusID,
) -> StatusResistIndex:
    """
    상태이상 저항 지수 계산.

    공식:
      저항 = CON + (보조 스탯 * 0.5)
      - 보조 스탯이 없는 경우: CON * 1.5
      - 소수점은 버림
      - 즉사(INSTANT_DEATH)는 저항 불가

    ⚠ 이 함수는 '저항 지수 계산'만 담당한다.
       실제 부여/해제 성공 여부는 checks.roll_status_success에서 처리.
    """
    if status_id not in STATUS_RESIST_STAT:
        raise ValueError(f"Unknown status_id: {status_id}")

    # 즉사: 저항 판정 자체가 없음
    if status_id == "INSTANT_DEATH":
        return StatusResistIndex(value=0, resistible=False)

    aux = STATUS_RESIST_STAT[status_id]

    con = int(stats.con)

    if aux is None:
        # CON * 1.5
        value = int(con * 1.5)
    else:
        aux_val = getattr(stats, aux.lower())
        value = int(con + aux_val * 0.5)

    return StatusResistIndex(value=value, resistible=True)
