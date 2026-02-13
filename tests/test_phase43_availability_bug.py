"""
Phase 43 — 행동 가용성 버그 재현 테스트

시나리오:
1. Actor가 SUB action(예: 이동)을 사용하여 can_sub=False 상태가 됨.
2. 인벤토리에 SUB action을 사용하는 아이템(예: 포션)이 있음.
3. list_use_items()가 해당 아이템을 ENABLED로 표시하는지 확인 (버그: ENABLED여서 문제).
4. build_turn_menu()가 ITEM_USE 노드를 ENABLED로 표시하는지 확인.
"""
from battle_system.core.models import BattleState, CharacterDef, Stats, ItemDef, UseSkillDef
from battle_system.core.commands import Step
from battle_system.core.types import CombatantID
from battle_system.engine.engine import BattleEngine
from battle_system.app.skill_ui import list_use_items
from battle_system.app.menu_builder import build_turn_menu

def test_reproduce_sub_slot_exhaustion_bug():
    # 1. Setup Actor with SUB action exhausted
    cid = CombatantID("HERO")
    
    # Define Potion (SUB action)
    potion_def = ItemDef(
        item_id="POTION",
        weight=1,
        use_skill=UseSkillDef(
            skill_id="USE:POTION",
            name="Potion",
            action_type="SUB",  # Requires SUB slot
            steps=[Step(kind="APPLY_HP_DELTA", target=None, hp_delta=30)]
        )
    )
    items_registry = {"POTION": potion_def}

    # BattleState
    bs = BattleState(
        defs={cid: CharacterDef(cid, "Hero", 1, Stats(10,10,10,10,10,10), 100)},
        combatants={},
        turn_order=[cid],
        turn_index=0,
        tick=0,
    )
    # Manually init combatant
    from battle_system.engine.engine import CombatantState
    bs.combatants[cid] = CombatantState(
        cid=cid, team="ALLY", _hp=50, max_hp=100, group_id="A",
        can_main=True, 
        can_sub=False  # <--- SUB exhausted
    )
    # Inventory has potion
    bs.inventory_snapshot[cid] = {"POTION": 1}

    # 2. Check list_use_items
    options = list_use_items(bs, cid, items_registry)
    
    # EXPECTATION: Potion should be DISABLED because can_sub is False
    # CURRENT BUG: It will probably be ENABLED because list_use_items doesn't check slots
    potion_opt = next(o for o in options if o.item_id == "POTION")
    
    print(f"\n[DEBUG] Potion enabled={potion_opt.enabled}, reason={potion_opt.reason}")
    
    # If bug exists, this assertion might fail (or pass if I write it to confirm the bug)
    # I want to FIX it, so I will write the assertion for the CORRECT behavior.
    assert potion_opt.enabled is False, "Potion should be disabled when SUB slot is empty"
    assert potion_opt.reason == "NO_ACTION_SLOT", f"Reason should be NO_ACTION_SLOT, got {potion_opt.reason}"

    # 3. Check Menu
    menu = build_turn_menu(bs, cid, [], items_registry=items_registry)
    item_node = menu.get_node("ITEM_USE")
    
    # If all items disabled, menu node should be disabled
    assert item_node.enabled is False, "ITEM_USE menu should be disabled if no usable items"
