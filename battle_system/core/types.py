from __future__ import annotations
from typing import Literal, NewType

CombatantID = NewType("CombatantID", str)
GroupID = NewType("GroupID", int)

TeamID = Literal["ALLY", "ENEMY"]
ActionType = Literal["MAIN", "SUB"]

AttackRange = Literal["MELEE", "RANGED"]
