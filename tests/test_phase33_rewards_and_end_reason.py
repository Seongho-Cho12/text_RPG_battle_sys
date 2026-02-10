# tests/test_phase33_rewards_and_end_reason.py

import random
import pytest

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import (
    BattleState,
    CharacterDef,
    CombatantState,
    Stats,
    DropEntry,
)
from battle_system.engine.engine import BattleEngine
from battle_system.engine.result import build_battle_result


def _mk_defs_and_states():
    """
    최소 BattleState 생성 유틸.
    - 2 allies (A1, A2)
    - 2 enemies (E1, E2)
    """
    a1 = CombatantID("A1")
    a2 = CombatantID("A2")
    e1 = CombatantID("E1")
    e2 = CombatantID("E2")

    g_ally = GroupID(0)
    g_enemy = GroupID(1)

    defs = {
        a1: CharacterDef(
            cid=a1,
            name="Ally1",
            level=1,
            stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10),
            max_hp=50,
            basic_attack_range="MELEE",
        ),
        a2: CharacterDef(
            cid=a2,
            name="Ally2",
            level=1,
            stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10),
            max_hp=50,
            basic_attack_range="MELEE",
        ),
        e1: CharacterDef(
            cid=e1,
            name="Enemy1",
            level=2,  # XP=40
            stats=Stats(str=5, agi=5, con=5, int=5, wis=5, cha=5),
            max_hp=30,
            basic_attack_range="MELEE",
            drops=[
                DropEntry(item_id="ITEM_CLAW", chance_percent=100, weight=1),
            ],
        ),
        e2: CharacterDef(
            cid=e2,
            name="Enemy2",
            level=3,  # XP=90
            stats=Stats(str=5, agi=5, con=5, int=5, wis=5, cha=5),
            max_hp=30,
            basic_attack_range="MELEE",
            drops=[
                DropEntry(item_id="ITEM_HORN", chance_percent=100, weight=2),
            ],
        ),
    }

    combatants = {
        a1: CombatantState(cid=a1, team="ALLY", max_hp=50, _hp=50, group_id=g_ally),
        a2: CombatantState(cid=a2, team="ALLY", max_hp=50, _hp=50, group_id=g_ally),
        e1: CombatantState(cid=e1, team="ENEMY", max_hp=30, _hp=0, group_id=g_enemy),
        e2: CombatantState(cid=e2, team="ENEMY", max_hp=30, _hp=0, group_id=g_enemy),
    }

    bs = BattleState(
        defs=defs,
        combatants=combatants,
        turn_order=[a1, a2, e1, e2],
        turn_index=0,
        tick=0,
        groups={g_ally: [a1, a2], g_enemy: [e1, e2]},
    )
    return bs, a1, a2, e1, e2


def test_phase33_check_battle_end_double_ko_is_enemy_victory():
    """
    네가 수정한 부분:
    - 아군/적군 모두 전멸(둘 다 alive 없음)인 경우도 ENEMY_VICTORY로 처리
    """
    bs, a1, a2, e1, e2 = _mk_defs_and_states()

    # 쌍방 전멸
    bs.combatants[a1].hp = 0
    bs.combatants[a2].hp = 0
    bs.combatants[e1].hp = 0
    bs.combatants[e2].hp = 0
    bs.ended = False
    bs.end_reason = None

    eng = BattleEngine()
    eng._check_battle_end(bs)

    assert bs.ended is True
    assert bs.end_reason == "ENEMY_VICTORY"


def test_phase33_build_battle_result_victory_rewards_xp_and_loot_are_merged():
    """
    - ALLY_VICTORY일 때:
      * xp_each_ally = sum(level^2*10 for downed enemies)
      * 몬스터 drops는 아군 각자에게 독립 판정(여기선 100%로 결정적)
      * 기존 inventory_delta(투척 -)와 드랍(+)이 합산됨
    """
    bs, a1, a2, e1, e2 = _mk_defs_and_states()

    # 아군 승리 상태로 확정
    bs.ended = True
    bs.end_reason = "ALLY_VICTORY"

    # 투척 등으로 이미 생긴 변화량(-)이 있다고 가정
    bs.inventory_delta = {
        a1: {"ITEM_ROCK": -1}
    }

    # 승리 보상 계산을 재현 가능하게(여기선 100%라 사실 의미 없음)
    rng = random.Random(123)

    result = build_battle_result(bs, events=["BATTLE_END"], rng=rng)

    # XP: 2^2*10=40, 3^2*10=90 => 130
    assert result.xp_each_ally == 130
    assert any("REWARD_XP_EACH_ALLY=130" in e for e in result.reward_events)

    inv = result.delta.inventory_delta

    # 드랍은 아군 각자에게 +1씩
    assert inv[a1]["ITEM_CLAW"] == 1
    assert inv[a1]["ITEM_HORN"] == 1
    assert inv[a2]["ITEM_CLAW"] == 1
    assert inv[a2]["ITEM_HORN"] == 1

    # 기존 투척(-)와 합산 유지
    assert inv[a1]["ITEM_ROCK"] == -1

    # 이벤트 로그 포함
    assert "BATTLE_END" in result.events


def test_phase33_build_battle_result_no_rewards_on_escape_or_defeat():
    """
    ESCAPE 또는 ENEMY_VICTORY면:
    - xp_each_ally=0
    - reward_events에 skipped
    - inventory_delta는 기존 값만 유지(드랍 추가 없음)
    """
    bs, a1, a2, e1, e2 = _mk_defs_and_states()

    # 케이스 1: ESCAPE
    bs.ended = True
    bs.end_reason = "ESCAPE"
    bs.inventory_delta = {a1: {"ITEM_ROCK": -1}}

    r1 = build_battle_result(bs, events=[], rng=random.Random(0))
    assert r1.xp_each_ally == 0
    assert any("skipped" in e for e in r1.reward_events)
    assert r1.delta.inventory_delta[a1]["ITEM_ROCK"] == -1
    assert "ITEM_CLAW" not in r1.delta.inventory_delta.get(a1, {})
    assert "ITEM_HORN" not in r1.delta.inventory_delta.get(a1, {})

    # 케이스 2: ENEMY_VICTORY
    bs.end_reason = "ENEMY_VICTORY"
    r2 = build_battle_result(bs, events=[], rng=random.Random(0))
    assert r2.xp_each_ally == 0
    assert any("skipped" in e for e in r2.reward_events)
    assert r2.delta.inventory_delta[a1]["ITEM_ROCK"] == -1
