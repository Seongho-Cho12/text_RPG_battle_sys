"""
Phase 42 — 실제 전투 실행 데모

Registry를 통해 Phase 41/42에서 생성한데이터를 로드하고,
Phase 40에서 만든 메뉴 기반 CLI 전투를 실행합니다.

사용법: python intro_battle_demo.py
"""
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (독립 실행 시 모듈 import 문제 해결)
root_dir = Path(__file__).resolve().parent
sys.path.append(str(root_dir))

from battle_system.engine.engine import BattleEngine
from battle_system.app.registry import Registry
from battle_system.app.battle_loop_cli import run_battle_cli

def main():
    # intro_battle_demo.py가 battle/ 내부에 있으므로, game_data는 상위 폴더(text_RPG)에 있음
    game_data_path = root_dir.parent / "game_data"
    
    if not game_data_path.exists():
        print(f"Error: game_data not found at {game_data_path}")
        return

    print(f"Loading game data from: {game_data_path}")

    registry = Registry(game_data_path)
    
    # 2명의 아군 vs 3명의 적 (같은 종 2마리 포함)
    setup = registry.build_battle_setup(
        allies=["HERO", "NPC_HEALER"],
        enemies=["GOBLIN_A", "GOBLIN_A", "GOBLIN_SHAMAN"],
    )

    print(f"Battle Setup Created:")
    print(f"  Allies: {[c.name for c in setup.allies]}")
    print(f"  Enemies: {[c.name for c in setup.enemies]}")
    print("-" * 60)

    engine = BattleEngine()
    bs = engine.create_battle(allies=setup.allies, enemies=setup.enemies)

    # 초기 인벤토리 설정
    for cid, inv in setup.initial_inventory.items():
        bs.inventory_snapshot[cid] = dict(inv)

    # 아이템 정의 로드 (사용 효과 등을 위해 필요)
    bs.items.update(setup.items)

    print("Starting Battle Loop...")
    final_bs = run_battle_cli(
        bs,
        engine=engine,
        skills_by_actor=setup.skills_by_actor,
        items_registry=setup.items,
    )

    print(f"Battle finished. Winner: {final_bs.end_reason}")

if __name__ == "__main__":
    main()
