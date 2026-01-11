import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.commands import Step
from battle_system.engine.engine import BattleEngine


def mk(cid: str, *, team_hint: str, level: int, agi: int, wis: int) -> CharacterDef:
    """
    TITLE: Phase 10/11 테스트용 캐릭터 생성
    SETUP:
      - 선턴 고정을 위해 A1이 높은 agi/wis를 갖도록 테스트에서 조절한다.
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
    TITLE: A1이 반드시 선턴이 되도록 구성된 1:1 전투 생성
    SETUP:
      - A1: agi/wis 크게
      - E1: agi/wis 작게
    EXPECTED:
      - bs.current_actor_id() == A1
    """
    a1 = mk("A1", team_hint="ALLY-", level=5, agi=30, wis=30)
    e1 = mk("E1", team_hint="ENEMY-", level=5, agi=10, wis=10)

    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1])

    assert bs.current_actor_id() == CombatantID("A1")
    return eng, bs


def test_phase10_main_cannot_be_used_twice_same_turn():
    """
    TITLE: 같은 턴에 MAIN 액션을 2번 쓰려 하면 실패해야 한다
    SETUP:
      - A1 선턴 1:1 전투
      - MAIN step 1개를 apply_steps로 실행하면 can_main이 False가 된다.
    STEPS:
      1) apply_steps([ATTACK], action_type=MAIN) 1회 -> 성공
      2) 같은 턴에 다시 apply_steps([ATTACK], action_type=MAIN) -> 예외
    EXPECTED:
      - 두 번째 호출에서 ValueError("Main action already used this turn.") 발생
      - turn은 end_turn을 호출하지 않는 한 넘어가지 않는다
    """
    eng, bs = _battle_1v1_a1_first()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    eng.apply_steps(bs, [Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="MAIN")])

    with pytest.raises(ValueError, match="Main action already used this turn"):
        eng.apply_steps(bs, [Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="MAIN")])


def test_phase10_sub_can_be_used_once_and_independent_from_main():
    """
    TITLE: MAIN과 SUB는 서로 독립이며, 각각 1번만 가능해야 한다
    SETUP:
      - A1 선턴 1:1 전투
      - MAIN 1회 사용 후 SUB 1회는 가능해야 한다.
      - SUB 2회는 불가능해야 한다.
    STEPS:
      1) MAIN으로 ATTACK 1회 실행
      2) SUB으로 ATTACK 1회 실행(테스트 편의상 SUB도 공격으로 대체)
      3) SUB으로 ATTACK 1회 추가 실행 -> 실패해야 함
    EXPECTED:
      - 1),2) 성공
      - 3)에서 ValueError("Sub action already used this turn.") 발생
    """
    eng, bs = _battle_1v1_a1_first()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    eng.apply_steps(bs, [Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="MAIN")])
    eng.apply_steps(bs, [Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="SUB")])

    with pytest.raises(ValueError, match="Sub action already used this turn"):
        eng.apply_steps(bs, [Step(kind="ATTACK", actor=A1, target=E1, reaction_immune=False, action_type="SUB")])


def test_phase11_end_turn_decrements_cooldowns_for_all_combatants():
    """
    TITLE: end_turn()이 호출될 때마다 전원 cooldown이 1씩 감소해야 한다 (전역 tick 규칙)
    SETUP:
      - A1 선턴 1:1 전투
      - A1과 E1에 각각 cooldown을 임의로 심는다.
        A1.cooldowns["X"]=2, E1.cooldowns["Y"]=1
      - end_turn을 1회 호출하면:
        A1.X: 2->1
        E1.Y: 1->0(삭제)
    STEPS:
      1) cooldown 주입
      2) end_turn 1회
      3) 값 확인
      4) end_turn 1회 추가
      5) A1.X 삭제 확인
    EXPECTED:
      - tick이 2번 증가한다
      - cooldown은 전원 감소/만료 삭제된다
    """
    eng, bs = _battle_1v1_a1_first()
    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    bs.combatants[A1].cooldowns["X"] = 2
    bs.combatants[E1].cooldowns["Y"] = 1

    tick0 = bs.tick

    eng.end_turn(bs)  # tick +1
    assert bs.tick == tick0 + 1
    assert bs.combatants[A1].cooldowns["X"] == 1
    assert "Y" not in bs.combatants[E1].cooldowns  # 만료로 삭제

    eng.end_turn(bs)  # tick +1
    assert bs.tick == tick0 + 2
    assert "X" not in bs.combatants[A1].cooldowns
