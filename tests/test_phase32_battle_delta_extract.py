import pytest

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import BattleState, CharacterDef, CombatantState, Stats
from battle_system.engine.result import extract_battle_delta


def _mk_bs():
    a = CombatantID("A")
    b = CombatantID("B")
    g1 = GroupID(1)
    g2 = GroupID(2)

    defs = {
        a: CharacterDef(
            cid=a, name="A", level=1,
            stats=Stats(str=5, agi=5, con=5, int=5, wis=5, cha=5),
            max_hp=50, basic_attack_range="MELEE",
        ),
        b: CharacterDef(
            cid=b, name="B", level=1,
            stats=Stats(str=5, agi=5, con=5, int=5, wis=5, cha=5),
            max_hp=50, basic_attack_range="MELEE",
        ),
    }

    combatants = {
        a: CombatantState(cid=a, team="ALLY", max_hp=50, _hp=37, group_id=g1),
        b: CombatantState(cid=b, team="ENEMY", max_hp=50, _hp=0, group_id=g2),
    }

    bs = BattleState(
        defs=defs,
        combatants=combatants,
        turn_order=[a, b],
        turn_index=0,
        tick=0,
        groups={g1: [a], g2: [b]},
    )
    return bs, a, b


def test_phase32_extract_battle_delta_requires_ended():
    bs, a, b = _mk_bs()
    bs.ended = False
    bs.end_reason = None
    with pytest.raises(ValueError):
        extract_battle_delta(bs)


def test_phase32_extract_battle_delta_hp_and_inventory_delta_snapshot():
    bs, a, b = _mk_bs()

    # 전투 종료 상태
    bs.ended = True
    bs.end_reason = "ALLY_VICTORY"

    # 투척 등으로 생긴 변화량이 이미 누적되어 있다고 가정
    bs.inventory_delta = {
        a: {"ITEM_ROCK": -1, "ITEM_CLAW": +2},
    }

    delta = extract_battle_delta(bs)

    assert delta.hp_after[a] == 37
    assert delta.hp_after[b] == 0

    assert delta.inventory_delta[a]["ITEM_ROCK"] == -1
    assert delta.inventory_delta[a]["ITEM_CLAW"] == 2

    # deepcopy 보장(원본 바꿔도 delta는 안 바뀌어야 함)
    bs.inventory_delta[a]["ITEM_ROCK"] = -999
    assert delta.inventory_delta[a]["ITEM_ROCK"] == -1
