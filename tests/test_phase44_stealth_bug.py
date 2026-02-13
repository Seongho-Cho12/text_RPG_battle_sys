"""
Phase 44 — 은밀 일격(Stealth Strike) 버그 재현 테스트

시나리오:
1. HERO에게 SK_STEALTH_STRIKE 스킬이 있음.
2. TACTICAL_STEALTH step은 target 필드가 없음 (None).
3. instantiate_skill_with_inputs()는 TACTICAL_STEALTH에 대해 target을 채우지 않음.
4. engine.apply_skill() 실행 시 _resolve_anchor()에서 ValueError 발생 확인.
"""
import pytest
from battle_system.core.models import BattleState, CharacterDef, Stats
from battle_system.core.types import CombatantID
from battle_system.engine.engine import BattleEngine
from battle_system.app.registry import Registry
from battle_system.app.skill_ui import instantiate_skill_with_inputs

# Mock Registry to load specific skill content if needed, 
# or use actual file if available.
# Here we can load actual file since it exists.

def test_reproduce_stealth_strike_bug():
    # 1. Setup
    cid = CombatantID("HERO")
    engine = BattleEngine()
    
    # Load actual YAML from disk to confirm file content issue
    from pathlib import Path
    import yaml
    
    skill_path = Path("D:/AI_RPG/research_1/text_RPG/game_data/characters/HERO/skills/SK_STEALTH_STRIKE.yaml")
    if not skill_path.exists():
        pytest.skip("SK_STEALTH_STRIKE.yaml not found")
        
    from battle_system.app.schema_io import load_skill_def
    skill = load_skill_def(skill_path, actor=cid)
    # skill.actor = cid # already set by load_skill_def
    
    # BattleState
    bs = BattleState(
        defs={cid: CharacterDef(cid, "Hero", 1, Stats(10,10,10,10,10,10), 100)},
        combatants={},
        turn_order=[cid],
        turn_index=0,
        tick=0,
    )
    from battle_system.engine.engine import CombatantState
    bs.combatants[cid] = CombatantState(
        cid=cid, team="ALLY", _hp=100, max_hp=100, group_id="A",
        can_main=True, can_sub=True
    )
    # Dummy enemy
    eid = CombatantID("ENEMY")
    bs.combatants[eid] = CombatantState(
        cid=eid, team="ENEMY", _hp=100, max_hp=100, group_id="A"
    )
    bs.defs[eid] = CharacterDef(eid, "Enemy", 1, Stats(10,10,10,10,10,10), 100)
    
    # 2. Instantiate with input (Targeting Enemy for the ATTACK step)
    # TACTICAL_STEALTH step doesn't require target, so it remains None
    concrete = instantiate_skill_with_inputs(skill, target=eid)
    
    # Verify first step has target='SELF' (fix verified)
    assert concrete.steps[0].kind == "TACTICAL_STEALTH"
    assert concrete.steps[0].target == "SELF"
    
    # 3. Apply Skill -> Expect Success
    outcome = engine.apply_skill(bs, concrete)
    
    # Verify events
    events_str = "\n".join(outcome.events)
    print(f"Outcome events:\n{events_str}")
    
    assert "STEALTH_APPLIED" in events_str
    assert "STEP: ATTACK" in events_str
