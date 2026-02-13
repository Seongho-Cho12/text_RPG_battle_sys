from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Any

import yaml

from battle_system.engine.engine import BattleEngine
from battle_system.app.registry import load_registry
from battle_system.app.battle_runner import run_battle


# ---------------------------
# YAML IO
# ---------------------------

def read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def write_yaml(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


# ---------------------------
# Schema patching (apply results)
# ---------------------------

def apply_hp_to_character_yaml(char_yaml: dict, hp_after: int) -> None:
    """
    character.yaml에 HP를 반영한다.
    - 권장 스키마: hp: { current: int, max: int }
    - 만약 current가 없으면 만들어서 넣는다.
    """
    hp_block = char_yaml.get("hp")
    if hp_block is None or not isinstance(hp_block, dict):
        # 최소한 max는 기존 문서/코드에서 쓰고 있으니 맞춰서 생성
        max_hp = None
        try:
            max_hp = int(char_yaml.get("max_hp", 0))
        except Exception:
            max_hp = 0
        char_yaml["hp"] = {"current": int(hp_after), "max": int(max_hp)}
        return

    # max는 유지, current만 반영
    hp_block["current"] = int(hp_after)

def apply_inventory_delta_to_character_yaml(char_yaml: dict, inv_delta: Dict[str, int]) -> None:
    """
    character.yaml의 inventory 딕셔너리에 delta를 누적 반영하고,
    0 이하가 되면 0으로 clamp한다.
    """
    inv = char_yaml.get("inventory")
    if inv is None or not isinstance(inv, dict):
        inv = {}
        char_yaml["inventory"] = inv

    for item_id, d in inv_delta.items():
        cur = int(inv.get(item_id, 0))
        nxt = cur + int(d)
        if nxt < 0:
            nxt = 0
        inv[item_id] = nxt

def apply_xp_to_character_yaml(char_yaml: dict, xp: int) -> None:
    """
    character.yaml에 경험치를 반영한다.
    스키마에 exp가 없으면 생성한다.
    """
    cur = int(char_yaml.get("exp", 0))
    char_yaml["exp"] = cur + int(xp)


# ---------------------------
# CLI
# ---------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=str, default="game_data", help="game_data 루트 경로")
    p.add_argument("--allies", type=str, required=True, help="아군 cid 콤마구분 (예: A,B)")
    p.add_argument("--enemies", type=str, required=True, help="적군 cid 콤마구분 (예: GOBLIN_01)")
    p.add_argument("--writeback", action="store_true", help="전투 종료 후 character.yaml에 결과 반영")
    return p.parse_args()

def split_ids(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main() -> None:
    args = parse_args()
    data_root = Path(args.data)

    reg = load_registry(data_root)
    allies = split_ids(args.allies)
    enemies = split_ids(args.enemies)

    setup = reg.build_battle_setup(allies=allies, enemies=enemies)

    engine = BattleEngine()
    result = run_battle(
        engine=engine,
        allies=setup.allies,
        enemies=setup.enemies,
        skills_by_actor=setup.skills_by_actor,
        initial_inventory=setup.initial_inventory,
        items=setup.items,  # ✅ TACTICAL_THROW weight 참조용
    )

    print("\n=== BATTLE ENDED ===")
    print("end_reason:", result.end_reason)
    print("hp_after:", dict(result.hp_after))
    print("inventory_delta:", {cid: dict(d) for cid, d in result.inventory_delta.items()})
    print("xp_each_ally:", result.xp_each_ally)

    if not args.writeback:
        print("\n(writeback 없음) character.yaml은 수정하지 않았습니다. --writeback을 켜면 반영됩니다.")
        return

    # ---------------------------
    # Write-back to character.yaml
    # ---------------------------
    print("\n=== WRITEBACK ===")

    # ally xp는 “아군만” 반영
    ally_set = set(allies)

    for cid in allies + enemies:
        char_path = data_root / "characters" / cid / "character.yaml"
        if not char_path.exists():
            raise FileNotFoundError(f"character.yaml not found: {char_path}")

        y = read_yaml(char_path)

        # HP 반영 (전투에 참가한 cid만 존재해야 함)
        if cid in result.hp_after:
            apply_hp_to_character_yaml(y, result.hp_after[cid])

        # 인벤 delta 반영
        inv_d = result.inventory_delta.get(cid)
        if inv_d:
            apply_inventory_delta_to_character_yaml(y, inv_d)

        # XP 반영(아군만)
        if cid in ally_set and result.xp_each_ally:
            apply_xp_to_character_yaml(y, result.xp_each_ally)

        write_yaml(char_path, y)
        print(f"- updated: {cid} -> {char_path}")

    print("\n완료: 전투 결과가 character.yaml에 반영되었습니다.")


if __name__ == "__main__":
    main()
