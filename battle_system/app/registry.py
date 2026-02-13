# battle_system/app/registry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from battle_system.core.models import CharacterDef, Stats
from battle_system.core.commands import Skill, Step

from battle_system.app.schema_io import (
    load_item_defs,
    load_skill_def,
    load_skill_defs_from_dir,
    load_character_state,
)


@dataclass(frozen=True)
class BattleSetup:
    allies: List[CharacterDef]
    enemies: List[CharacterDef]
    skills_by_actor: Dict[str, List[Skill]]
    initial_inventory: Dict[str, Dict[str, int]]
    items: Dict[str, object]  # 실제 타입 ItemDef dict


def _stats_from_yaml(st: dict) -> Stats:
    return Stats(
        str=int(st["STR"]),
        agi=int(st["AGI"]),
        con=int(st["CON"]),
        int=int(st["INT"]),
        wis=int(st["WIS"]),
        cha=int(st.get("CHA", 0)),
    )


def _make_basic_attack(*, actor: str, crit_stat: str, range_: str) -> Skill:
    return Skill(
        skill_id="BASIC_ATTACK",
        name="Basic Attack",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        crit_stat=crit_stat,  # type: ignore[arg-type]
        steps=[Step(kind="ATTACK", target=None, range=range_, area="SINGLE")],  # type: ignore[arg-type]
    )


def _weapon_item_id(char_state: dict) -> Optional[str]:
    eq = char_state.get("equipment") or {}
    # 기본은 RIGHT_HAND를 무기로 가정
    return eq.get("RIGHT_HAND")


def _is_monster(char_state: dict) -> bool:
    return (char_state.get("kind") == "MONSTER")


def load_registry(game_data_dir: str | Path) -> "Registry":
    return Registry(Path(game_data_dir))


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items = load_item_defs(root / "items")
        self.base_skills_dir = root / "skills_base"
        self.characters_dir = root / "characters"

        # 공통 스킬은 템플릿(engage/disengage/escape)만 로드해 둔다.
        # basic_attack은 actor별 생성이라 여기서 로드 안 함.
        self._base_templates = {
            "ENGAGE": self.base_skills_dir / "engage.yaml",
            "DISENGAGE": self.base_skills_dir / "disengage.yaml",
            "ESCAPE": self.base_skills_dir / "escape.yaml",
        }

    def build_battle_setup(self, *, allies: List[str], enemies: List[str]) -> BattleSetup:
        ids = allies + enemies

        defs: Dict[str, CharacterDef] = {}
        skills_by_actor: Dict[str, List[Skill]] = {}
        initial_inventory: Dict[str, Dict[str, int]] = {}

        for cid in ids:
            cdir = self.characters_dir / cid
            state = load_character_state(cdir / "character.yaml")

            # CharacterDef 생성
            st = _stats_from_yaml(state["stats"])
            max_hp = int(state["hp"]["max"])
            defs[cid] = CharacterDef(
                cid=cid,
                name=state.get("name", cid),
                level=int(state.get("level", 1)),
                stats=st,
                max_hp=max_hp,
            )

            # 인벤 초기값
            inv = {k: int(v) for k, v in (state.get("inventory") or {}).items()}
            initial_inventory[cid] = inv

            # 기본 스킬 3개(파일 로드)
            base = [
                load_skill_def(self._base_templates["ENGAGE"], actor=cid),
                load_skill_def(self._base_templates["DISENGAGE"], actor=cid),
                load_skill_def(self._base_templates["ESCAPE"], actor=cid),
            ]

            # basic_attack 생성(캐릭터=무기 attack_profile, 몬스터=base_attack)
            if _is_monster(state):
                ba = state["base_attack"]
                basic_attack = _make_basic_attack(actor=cid, crit_stat=ba["crit_stat"], range_=ba["range"])
                weapon_type = None
            else:
                wid = _weapon_item_id(state)
                if not wid:
                    raise ValueError(f"{cid}: weapon is required (equipment.RIGHT_HAND missing)")
                if wid not in self.items:
                    raise ValueError(f"{cid}: unknown weapon item_id {wid}")
                item = self.items[wid]
                if item.attack_profile is None:
                    raise ValueError(f"{cid}: weapon {wid} missing attack_profile")
                basic_attack = _make_basic_attack(
                    actor=cid,
                    crit_stat=item.attack_profile.crit_stat,
                    range_=item.attack_profile.range,
                )
                weapon_type = item.weapon_type

            # 고유 스킬(캐릭터 디렉토리)
            personal = load_skill_defs_from_dir(cdir / "skills", actor=cid)

            # 캐릭터만 무기 호환 필터 적용 (MONSTER는 무시)
            if not _is_monster(state):
                if weapon_type is None:
                    raise ValueError(f"{cid}: weapon_type is required for weapon compatibility filtering")

                personal = [
                    sk for sk in personal
                    # allowed_weapon_types가 비어있으면(=제약 없음) 허용
                    if (not sk.allowed_weapon_types) or (weapon_type in sk.allowed_weapon_types)
                ]

            skills_by_actor[cid] = [basic_attack] + base + personal

        return BattleSetup(
            allies=[defs[c] for c in allies],
            enemies=[defs[c] for c in enemies],
            skills_by_actor=skills_by_actor,
            initial_inventory=initial_inventory,
            items=self.items,
        )
