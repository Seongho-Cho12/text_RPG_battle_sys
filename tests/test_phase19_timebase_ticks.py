import random
import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import Stats, CharacterDef
from battle_system.core.types import CombatantID
from battle_system.core.commands import Step, Skill
from battle_system.timebase.durations import turns_to_ticks_for_battle


def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int = 50) -> CharacterDef:
    # ✅ name 필수
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def test_phase19_1_effect_turns_to_ticks_saved_correctly():
    """
    1) 상태이상 지속시간(턴)을 입력하면, 엔진이 tick으로 변환해 저장하는지 검증.
    - 2인 전투(참여인원=2)에서 effect_duration=2(턴) => ticks=2*2+1=5
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    # resist=0이 되도록 CON=0 (POISONED는 보조스탯 없음 => resist=int(CON*1.5)=0)
    e1 = _mk_char("E1", level=5, stats=Stats(str=0, agi=0, con=0, int=0, wis=0, cha=0))

    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    eff = "POISONED"
    turns = 2
    inflict = 10

    expected_ticks = turns_to_ticks_for_battle(bs, turns)
    assert expected_ticks == 5  # 2*2+1

    random.seed(0)
    skill = Skill(
        skill_id="test_eff_1",
        name="test_eff_1",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[Step(
            kind="APPLY_EFFECT",
            target=tgt,
            effect_id=eff,
            effect_duration=turns,     # ✅ 턴 입력
            status_inflict=inflict,
        )],
    )
    out = eng.apply_skill(bs, skill)

    print("\n[Phase19-1] effect saved")
    for e in out.events:
        print(" ", e)

    assert bs.combatants[tgt].effects[eff] == expected_ticks


def test_phase19_2_end_turn_decrements_ticks_each_turn():
    """
    2) 턴이 지날 때마다(= end_turn 호출마다) effects/cooldowns tick이 전원 -1 되는지 검증.
    - effect_duration=2턴 => 5 ticks 저장 후 end_turn 1회 => 4가 되어야 함.
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=0, agi=0, con=0, int=0, wis=0, cha=0))
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    eff = "POISONED"
    turns = 2
    inflict = 10

    random.seed(1)
    skill = Skill(
        skill_id="test_eff_2",
        name="test_eff_2",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[Step(
            kind="APPLY_EFFECT",
            target=tgt,
            effect_id=eff,
            effect_duration=turns,
            status_inflict=inflict,
        )],
    )
    eng.apply_skill(bs, skill)

    assert bs.combatants[tgt].effects[eff] == 5

    eng.end_turn(bs)  # tick -1 전원
    assert bs.combatants[tgt].effects[eff] == 4


def test_phase19_3_ticks_reach_zero_then_deleted():
    """
    3) tick이 0이 되면 effects/cooldowns에서 삭제되는지 검증.
    - 2턴(5ticks) 부여 후 end_turn을 5번 호출하면 삭제되어야 함.
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=0, agi=0, con=0, int=0, wis=0, cha=0))
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    eff = "POISONED"
    turns = 2
    inflict = 10

    random.seed(2)
    skill = Skill(
        skill_id="test_eff_3",
        name="test_eff_3",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[Step(
            kind="APPLY_EFFECT",
            target=tgt,
            effect_id=eff,
            effect_duration=turns,
            status_inflict=inflict,
        )],
    )
    eng.apply_skill(bs, skill)
    assert bs.combatants[tgt].effects[eff] == 5

    for _ in range(5):
        eng.end_turn(bs)

    assert eff not in bs.combatants[tgt].effects


def test_phase19_4_cooldown_1turn_blocks_on_next_own_turn_then_expires():
    """
    4) 쿨다운 1턴:
       - 사용 후 ticks=1*participants+1=3 저장
       - end_turn 2번(상대 턴 포함) 지나 내 다음 턴이 되면 ticks_left=1이라 사용 불가
       - 그 턴을 넘기면 0이 되어 cooldown 삭제 -> 이후 사용 가능

    ⚠ 주의(요구사항 반영):
       - 같은 스킬은 한 턴에 2번 못 쓰므로 '같은 턴 연속 사용'으로 테스트하지 않는다.
       - "내 다음 턴"에서 막히는지 확인한다.
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=10, stats=Stats(str=1, agi=10, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=1, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    bs = eng.create_battle([a1], [e1])

    actor = CombatantID("A1")
    skill_id = "S_TEST"
    cd_turns = 1
    expected_cd_ticks = turns_to_ticks_for_battle(bs, cd_turns)
    assert expected_cd_ticks == 3

    # A1 턴: 스킬 사용(ATTACK에 cooldown 부착 — Skill 레벨에서 처리)
    skill = Skill(
        skill_id=skill_id,
        name="test_cd_skill",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        cooldown_turns=cd_turns,
        steps=[Step(kind="ATTACK", target=CombatantID("E1"))],
    )
    out1 = eng.apply_skill(bs, skill)
    assert bs.combatants[actor].cooldowns[skill_id] == 3

    # A1 턴 종료: -1 => 2
    eng.end_turn(bs)
    assert bs.combatants[actor].cooldowns[skill_id] == 2

    # E1 턴 종료: -1 => 1 (이제 A1 턴로 돌아옴)
    eng.end_turn(bs)
    assert bs.current_actor_id() == actor
    assert bs.combatants[actor].cooldowns[skill_id] == 1

    # A1의 다음 턴: 아직 1 남았으므로 사용 불가
    with pytest.raises(ValueError):
        skill_blocked = Skill(
            skill_id=skill_id,
            name="test_cd_skill",
            actor=bs.current_actor_id(),
            action_type="MAIN",
            cooldown_turns=cd_turns,
            steps=[Step(kind="ATTACK", target=CombatantID("E1"))],
        )
        eng.apply_skill(bs, skill_blocked)

    # 이 턴을 넘기면 0이 되어 삭제
    eng.end_turn(bs)
    assert skill_id not in bs.combatants[actor].cooldowns

    print("\n[Phase19-4] cooldown events")
    for e in out1.events:
        print(" ", e)


def test_phase19_5_reapply_same_effect_adds_duration_ticks():
    """
    5) 동일 effect_id를 다시 걸면 '덮어쓰기'가 아니라 tick이 누적되는지 검증.
    - 2인 전투에서 1턴 부여 tick=3
    - 1턴을 2번 걸면 total=6 (단, 2번째 적용 전 end_turn 2회로 tick이 1 감소)
    """
    eng = BattleEngine()
    a1 = _mk_char("A1", level=5, stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0))
    e1 = _mk_char("E1", level=5, stats=Stats(str=0, agi=0, con=0, int=0, wis=0, cha=0))
    bs = eng.create_battle([a1], [e1])

    tgt = CombatantID("E1")
    eff = "POISONED"
    inflict = 10
    turns = 1
    ticks_per_apply = turns_to_ticks_for_battle(bs, turns)
    assert ticks_per_apply == 3

    random.seed(10)
    skill1 = Skill(
        skill_id="test_reapply_1",
        name="test_reapply_1",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[Step(
            kind="APPLY_EFFECT",
            target=tgt,
            effect_id=eff,
            effect_duration=turns,
            status_inflict=inflict,
        )],
    )
    out1 = eng.apply_skill(bs, skill1)
    assert bs.combatants[tgt].effects[eff] == 3

    eng.end_turn(bs)  # -1 => 2
    eng.end_turn(bs)  # -1 => 1

    random.seed(11)
    skill2 = Skill(
        skill_id="test_reapply_2",
        name="test_reapply_2",
        actor=bs.current_actor_id(),
        action_type="MAIN",
        steps=[Step(
            kind="APPLY_EFFECT",
            target=tgt,
            effect_id=eff,
            effect_duration=turns,
            status_inflict=inflict,
        )],
    )
    out2 = eng.apply_skill(bs, skill2)
    # 1(기존) + 3(새로 추가) = 4
    assert bs.combatants[tgt].effects[eff] == 4

    print("\n[Phase19-5] effect reapply adds ticks")
    for e in out1.events:
        print(" ", e)
    for e in out2.events:
        print(" ", e)
