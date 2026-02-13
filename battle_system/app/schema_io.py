# battle_system/app/schema_io.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml  # PyYAML 필요

from battle_system.core.models import CharacterDef, Stats, ItemDef, AttackProfile
from battle_system.core.commands import Skill, Step


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_item_defs(items_dir: str | Path) -> Dict[str, ItemDef]:
    items_dir = Path(items_dir)
    out: Dict[str, ItemDef] = {}
    for p in sorted(items_dir.glob("*.yaml")):
        d = _read_yaml(p)
        item_id = d["item_id"]
        weight = int(d.get("weight", 0))
        weapon_type = d.get("weapon_type")
        ap = d.get("attack_profile")
        attack_profile = None
        if ap is not None:
            attack_profile = AttackProfile(
                crit_stat=ap["crit_stat"],
                range=ap["range"],
            )
        out[item_id] = ItemDef(
            item_id=item_id,
            weight=weight,  # type: ignore[arg-type]
            weapon_type=weapon_type,
            attack_profile=attack_profile,
        )
    return out


def load_skill_def(path: str | Path, *, actor: str) -> Skill:
    p = Path(path)
    d = _read_yaml(p)
    steps = []
    for sd in d.get("steps", []):
        steps.append(Step(**sd))
    return Skill(
        skill_id=d["skill_id"],
        name=d.get("name", d["skill_id"]),
        actor=actor,
        allowed_weapon_types=list(d.get("allowed_weapon_types") or []),
        action_type=d.get("action_type", "MAIN"),
        cooldown_turns=int(d.get("cooldown_turns", 0)),
        steps=steps,
        crit_stat=d.get("crit_stat", "STR"),
        target_filter=d.get("target_filter", "ANY"),
    )


def load_skill_defs_from_dir(skills_dir: str | Path, *, actor: str) -> List[Skill]:
    skills_dir = Path(skills_dir)
    skills: List[Skill] = []
    for p in sorted(skills_dir.glob("*.yaml")):
        skills.append(load_skill_def(p, actor=actor))
    return skills


def load_character_state(path: str | Path) -> dict:
    return _read_yaml(Path(path))
