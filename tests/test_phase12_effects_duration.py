import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.engine.engine import BattleEngine


def mk(cid: str, *, team_hint: str, level: int, agi: int, wis: int) -> CharacterDef:
    """
    TITLE: Phase 12 effects duration 테스트용 캐릭터 생성
    SETUP:
      - A1이 선턴이 되도록 A1의 agi/wis를 높게 준다.
      - max_hp는 충분히 크게 둔다.
    EXPECTED:
      - create_battle에 바로 넣을 수 있다.
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=level,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=60,
        basic_attack_range="MELEE",
    )


def _battle_1v1_a1_first():
    """
    TITLE: A1이 선턴인 1:1 전투 생성
    SETUP:
      - A1(ALLY): agi/wis 높음
      - E1(ENEMY): agi/wis 낮음
    EXPECTED:
      - current actor는 A1
    """
    a1 = mk("A1", team_hint="ALLY-", level=5, agi=30, wis=30)
    e1 = mk("E1", team_hint="ENEMY-", level=5, agi=10, wis=10)
    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1])
    assert bs.current_actor_id() == CombatantID("A1")
    return eng, bs


def test_phase12_end_turn_decrements_effects_for_all_combatants_and_expires():
    """
    TITLE: end_turn()마다 전원의 effects duration이 1씩 감소하고 0 이하가 되면 자동 삭제되는지 검증
    SETUP:
      - A1, E1에 각각 effects를 주입한다.
        A1.effects["BLEED"]=2
        E1.effects["BURN"]=1
      - end_turn 1회:
        A1.BLEED 2->1
        E1.BURN 1->0 => 삭제
      - end_turn 1회 추가:
        A1.BLEED 1->0 => 삭제
    STEPS:
      1) effects 주입
      2) end_turn 1회 후 값 확인
      3) end_turn 1회 추가 후 값 확인
    EXPECTED:
      - 전역 tick도 2 증가
      - 만료된 effect는 dict에서 제거된다
    """
    eng, bs = _battle_1v1_a1_first()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    bs.combatants[A1].effects["BLEED"] = 2
    bs.combatants[E1].effects["BURN"] = 1

    t0 = bs.tick

    eng.end_turn(bs)
    assert bs.tick == t0 + 1
    assert bs.combatants[A1].effects["BLEED"] == 1
    assert "BURN" not in bs.combatants[E1].effects

    eng.end_turn(bs)
    assert bs.tick == t0 + 2
    assert "BLEED" not in bs.combatants[A1].effects
