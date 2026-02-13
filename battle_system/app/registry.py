# battle_system/app/registry.py
from __future__ import annotations

from collections import Counter
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
    return eq.get("RIGHT_HAND")


def _is_monster(char_state: dict) -> bool:
    return (char_state.get("kind") == "MONSTER")


def load_registry(game_data_dir: str | Path) -> "Registry":
    return Registry(Path(game_data_dir))


def _make_instance_ids(template_ids: List[str]) -> List[Tuple[str, str]]:
    """
    몬스터 템플릿 ID 목록 → (instance_cid, template_id) 쌍 목록.

    규칙:
    - 같은 template가 1개뿐이면: instance_cid = template_id 그대로
    - 같은 template가 2개 이상이면: instance_cid = "TEMPLATE#1", "TEMPLATE#2", ...
    """
    counts = Counter(template_ids)
    seen: Dict[str, int] = {}

    result: List[Tuple[str, str]] = []
    for tid in template_ids:
        if counts[tid] == 1:
            result.append((tid, tid))
        else:
            n = seen.get(tid, 0) + 1
            seen[tid] = n
            result.append((f"{tid}#{n}", tid))
    return result


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items = load_item_defs(root / "items")
        self.base_skills_dir = root / "skills_base"
        self.characters_dir = root / "characters"
        self.monsters_dir = root / "monsters"

        self._base_templates = {
            "ENGAGE": self.base_skills_dir / "engage.yaml",
            "DISENGAGE": self.base_skills_dir / "disengage.yaml",
            "ESCAPE": self.base_skills_dir / "escape.yaml",
        }

    def _load_base_skills(self, actor: str) -> List[Skill]:
        return [
            load_skill_def(self._base_templates["ENGAGE"], actor=actor),
            load_skill_def(self._base_templates["DISENGAGE"], actor=actor),
            load_skill_def(self._base_templates["ESCAPE"], actor=actor),
        ]

    def _load_character(
        self,
        cid: str,
        *,
        defs: Dict[str, CharacterDef],
        skills_by_actor: Dict[str, List[Skill]],
        initial_inventory: Dict[str, Dict[str, int]],
    ) -> None:
        """characters/ 디렉토리에서 PLAYER/NPC 로드."""
        cdir = self.characters_dir / cid
        state = load_character_state(cdir / "character.yaml")

        st = _stats_from_yaml(state["stats"])
        max_hp = int(state["hp"]["max"])
        defs[cid] = CharacterDef(
            cid=cid,
            name=state.get("name", cid),
            level=int(state.get("level", 1)),
            stats=st,
            max_hp=max_hp,
        )

        inv = {k: int(v) for k, v in (state.get("inventory") or {}).items()}
        initial_inventory[cid] = inv

        # 무기에서 basic_attack 유도
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

        # 고유 스킬 + 무기 호환 필터
        personal = load_skill_defs_from_dir(cdir / "skills", actor=cid)
        if weapon_type is None:
            raise ValueError(f"{cid}: weapon_type is required for weapon compatibility filtering")
        personal = [
            sk for sk in personal
            if (not sk.allowed_weapon_types) or (weapon_type in sk.allowed_weapon_types)
        ]

        skills_by_actor[cid] = [basic_attack] + self._load_base_skills(cid) + personal

    def _load_monster_instance(
        self,
        instance_cid: str,
        template_id: str,
        *,
        defs: Dict[str, CharacterDef],
        skills_by_actor: Dict[str, List[Skill]],
        initial_inventory: Dict[str, Dict[str, int]],
    ) -> None:
        """monsters/ 디렉토리에서 템플릿 로드 → 인스턴스 CID로 생성."""
        mdir = self.monsters_dir / template_id
        state = load_character_state(mdir / "character.yaml")

        st = _stats_from_yaml(state["stats"])
        max_hp = int(state["hp"]["max"])
        defs[instance_cid] = CharacterDef(
            cid=instance_cid,
            name=state.get("name", template_id),
            level=int(state.get("level", 1)),
            stats=st,
            max_hp=max_hp,
        )

        inv = {k: int(v) for k, v in (state.get("inventory") or {}).items()}
        initial_inventory[instance_cid] = inv

        # 몬스터: base_attack에서 직접 basic_attack 생성
        ba = state["base_attack"]
        basic_attack = _make_basic_attack(
            actor=instance_cid,
            crit_stat=ba["crit_stat"],
            range_=ba["range"],
        )

        # 고유 스킬 (무기 호환 필터 무시)
        personal = load_skill_defs_from_dir(mdir / "skills", actor=instance_cid)

        skills_by_actor[instance_cid] = [basic_attack] + self._load_base_skills(instance_cid) + personal

    def build_battle_setup(
        self,
        *,
        allies: List[str],
        enemies: List[str],
    ) -> BattleSetup:
        """
        allies:  캐릭터 ID 목록 (characters/ 디렉토리)
        enemies: 몬스터 템플릿 ID 목록 (monsters/ 디렉토리, 중복 허용)
                 같은 템플릿이 1개면 CID = 그대로
                 같은 템플릿이 2개 이상이면 CID = "TEMPLATE#1", "TEMPLATE#2", ...
        """
        defs: Dict[str, CharacterDef] = {}
        skills_by_actor: Dict[str, List[Skill]] = {}
        initial_inventory: Dict[str, Dict[str, int]] = {}

        # 캐릭터 로드
        for cid in allies:
            self._load_character(
                cid,
                defs=defs,
                skills_by_actor=skills_by_actor,
                initial_inventory=initial_inventory,
            )

        # 몬스터 인스턴스 생성
        instance_pairs = _make_instance_ids(enemies)
        enemy_cids: List[str] = []

        for instance_cid, template_id in instance_pairs:
            self._load_monster_instance(
                instance_cid,
                template_id,
                defs=defs,
                skills_by_actor=skills_by_actor,
                initial_inventory=initial_inventory,
            )
            enemy_cids.append(instance_cid)

        return BattleSetup(
            allies=[defs[c] for c in allies],
            enemies=[defs[c] for c in enemy_cids],
            skills_by_actor=skills_by_actor,
            initial_inventory=initial_inventory,
            items=self.items,
        )
