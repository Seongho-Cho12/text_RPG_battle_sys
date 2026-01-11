import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.engine.engine import BattleEngine
from battle_system.formation.groups import same_group, can_melee, can_ranged


def mk(cid: str, lvl: int, agi: int, wis: int, team_hint: str = "") -> CharacterDef:
    """
    TITLE: 테스트용 캐릭터 정의(CharacterDef) 생성 헬퍼
    SETUP:
      - cid/name/level/Stats(6스탯)/max_hp/basic_attack_range를 채운 CharacterDef를 만든다.
      - 이번 Phase(1~4)에서는 스킬/장비/인벤토리 등은 사용하지 않는다.
      - Stats는 (str=10, con=10, int=10, cha=10)으로 고정하고,
        정렬/선공권에 필요한 (agi, wis)만 매개변수로 조정한다.
    EXPECTED:
      - 반환된 CharacterDef는 BattleEngine.create_battle 입력으로 바로 사용 가능하다.
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=lvl,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=10,
        basic_attack_range="MELEE",
    )


def test_phase1_turn_order_priority():
    """
    TITLE: 선공권(턴 오더) 정렬이 AGI -> WIS -> LEVEL 우선순위를 따르는지 검증
    SETUP:
      - Allies:
        - A: (lv=5, agi=10, wis=5)
      - Enemies:
        - B: (lv=1, agi=10, wis=9)
        - C: (lv=9, agi=9,  wis=20)
      - 정렬 규칙:
        1) agi 높은 순
        2) agi 동률이면 wis 높은 순
        3) agi, wis 동률이면 level 높은 순
    STEPS:
      1) BattleEngine.create_battle(allies=[A], enemies=[B, C])
      2) 결과 BattleState.turn_order를 확인
    EXPECTED:
      - B와 A는 agi=10으로 동률이므로 wis 비교:
        - B(wis=9) > A(wis=5) 이므로 B가 A보다 먼저
      - C는 agi=9로 낮으므로 마지막
      - 따라서 turn_order == [B, A, C]
    """
    a = mk("A", lvl=5, agi=10, wis=5, team_hint="ALLY-")
    b = mk("B", lvl=1, agi=10, wis=9, team_hint="ENEMY-")
    c = mk("C", lvl=9, agi=9, wis=20, team_hint="ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle(allies=[a], enemies=[b, c])

    assert [str(x) for x in bs.turn_order] == ["B", "A", "C"]


def test_phase1_team_split_and_initial_groups():
    """
    TITLE: 전투 생성 시 팀(ALLY/ENEMY) 구분과 초기 그룹(아군 1그룹/적군 1그룹) 구성이 맞는지 검증
    SETUP:
      - Allies:
        - A1: (lv=1, agi=5, wis=5)
        - A2: (lv=1, agi=6, wis=5)
      - Enemies:
        - E1: (lv=1, agi=4, wis=5)
      - 초기 그룹 규칙:
        - 전투 시작 시 아군은 아군끼리 뭉쳐 같은 그룹
        - 적군은 적군끼리 뭉쳐 같은 그룹
        - 아군 그룹과 적군 그룹은 서로 다른 그룹
    STEPS:
      1) create_battle(allies=[A1,A2], enemies=[E1])
      2) 각 CombatantState.team 확인
      3) same_group(A1,A2)와 same_group(A1,E1) 확인
    EXPECTED:
      - A1, A2는 team == "ALLY"
      - E1은 team == "ENEMY"
      - A1과 A2는 같은 그룹(same_group True)
      - A1과 E1은 다른 그룹(same_group False)
    """
    a1 = mk("A1", 1, 5, 5, "ALLY-")
    a2 = mk("A2", 1, 6, 5, "ALLY-")
    e1 = mk("E1", 1, 4, 5, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a1, a2], [e1])

    assert bs.combatants[CombatantID("A1")].team == "ALLY"
    assert bs.combatants[CombatantID("A2")].team == "ALLY"
    assert bs.combatants[CombatantID("E1")].team == "ENEMY"

    assert same_group(bs, CombatantID("A1"), CombatantID("A2")) is True
    assert same_group(bs, CombatantID("A1"), CombatantID("E1")) is False


def test_phase2_action_slots_and_turn_advance():
    """
    TITLE: 턴 슬롯(주행동 1회, 보조행동 1회) 소비와 턴 전환 시 슬롯 리셋을 검증
    SETUP:
      - Allies:
        - A: (lv=1, agi=10, wis=1)  # 선공권을 A가 잡도록 설정
      - Enemies:
        - E: (lv=1, agi=1, wis=1)
      - 슬롯 규칙:
        - 자신의 턴에 주행동 1회, 보조행동 1회 사용 가능
        - 턴 종료(end_turn) 시 다음 액터로 넘어가고, 그 액터 슬롯은 다시 True로 초기화
    STEPS:
      1) create_battle([A],[E])
      2) current_actor의 can_main/can_sub가 True인지 확인
      3) use_main, use_sub를 호출해 슬롯을 False로 소비
      4) end_turn 호출
      5) 다음 current_actor의 슬롯이 True로 초기화되었는지 확인
    EXPECTED:
      - 처음 current_actor의 can_main/can_sub == True
      - use_main/use_sub 후 둘 다 False
      - end_turn 후 current_actor가 바뀌고, 새 current_actor의 can_main/can_sub == True
    """
    a = mk("A", 1, 10, 1, "ALLY-")
    e = mk("E", 1, 1, 1, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a], [e])

    cur = bs.current_actor_id()
    st = bs.combatants[cur]
    assert st.can_main is True and st.can_sub is True

    eng.use_main(bs, cur)
    eng.use_sub(bs, cur)
    assert st.can_main is False and st.can_sub is False

    eng.end_turn(bs)
    cur2 = bs.current_actor_id()
    st2 = bs.combatants[cur2]
    assert st2.can_main is True and st2.can_sub is True


def test_phase2_cannot_act_out_of_turn():
    """
    TITLE: 자신의 턴이 아닐 때 행동(use_main)을 시도하면 실패하는지 검증
    SETUP:
      - Allies:
        - A: (lv=1, agi=10, wis=1)
      - Enemies:
        - E: (lv=1, agi=1,  wis=1)
      - 규칙:
        - 현재 차례(current_actor)가 아닌 캐릭터는 행동을 제출할 수 없다.
    STEPS:
      1) create_battle([A],[E])
      2) current_actor를 확인하고, 다른 쪽(other)을 계산
      3) other가 use_main을 시도하면 예외(ValueError)가 발생해야 함
    EXPECTED:
      - use_main(bs, other) 호출이 ValueError를 발생시킨다.
    """
    a = mk("A", 1, 10, 1, "ALLY-")
    e = mk("E", 1, 1, 1, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a], [e])

    cur = bs.current_actor_id()
    other = CombatantID("E") if str(cur) == "A" else CombatantID("A")

    with pytest.raises(ValueError):
        eng.use_main(bs, other)


def test_phase3_tick_and_duration_decrement():
    """
    TITLE: tick은 전역 시간이며, 각 턴 종료마다 모든 전투 참가자의 cooldown/effects가 1씩 감소하는지 검증
    SETUP:
      - Allies:
        - A: (lv=1, agi=10, wis=1)
      - Enemies:
        - E: (lv=1, agi=1,  wis=1)
      - 전역 tick 규칙:
        - "누구의 턴이든" end_turn()이 호출될 때마다 tick += 1
        - tick 1 증가마다 "모든 combatant"의 cooldown/effects가 1씩 감소
        - 0 이하가 되면 해당 항목은 제거
      - 사전 상태(감소 확인을 위해 양쪽 모두에 duration 심기):
        - A.cooldowns["skill_x"] = 2
        - A.effects["bleed"] = 1
        - E.cooldowns["skill_y"] = 1
        - E.effects["burn"] = 3
    STEPS:
      1) create_battle([A],[E])
      2) A/E에 위 duration 값을 세팅
      3) end_turn(bs) 1회 호출
    EXPECTED:
      - bs.tick: 0 -> 1
      - A:
        - skill_x: 2 -> 1 (유지)
        - bleed: 1 -> 0 (제거)
      - E:
        - skill_y: 1 -> 0 (제거)
        - burn: 3 -> 2 (유지)
    """
    a = mk("A", 1, 10, 1, "ALLY-")
    e = mk("E", 1, 1, 1, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a], [e])

    A = CombatantID("A")
    E = CombatantID("E")

    bs.combatants[A].cooldowns["skill_x"] = 2
    bs.combatants[A].effects["bleed"] = 1

    bs.combatants[E].cooldowns["skill_y"] = 1
    bs.combatants[E].effects["burn"] = 3

    assert bs.tick == 0
    eng.end_turn(bs)
    assert bs.tick == 1

    assert bs.combatants[A].cooldowns["skill_x"] == 1
    assert "bleed" not in bs.combatants[A].effects

    assert "skill_y" not in bs.combatants[E].cooldowns
    assert bs.combatants[E].effects["burn"] == 2


def test_phase4_range_rules():
    """
    TITLE: 사거리 규칙(근접=같은 그룹, 원거리=다른 그룹)이 초기 그룹 상태에서 올바르게 동작하는지 검증
    SETUP:
      - Allies:
        - A1: (lv=1, agi=5, wis=5)
        - A2: (lv=1, agi=6, wis=5)
      - Enemies:
        - E1: (lv=1, agi=4, wis=5)
      - 초기 그룹 규칙:
        - A1/A2는 같은 그룹(아군 그룹)
        - E1은 적군 그룹
      - 사거리 규칙:
        - 근접(can_melee): 같은 그룹만 True
        - 원거리(can_ranged): 다른 그룹만 True
    STEPS:
      1) create_battle([A1,A2],[E1])
      2) 같은 그룹(A1,A2)에서 can_melee/can_ranged 확인
      3) 다른 그룹(A1,E1)에서 can_melee/can_ranged 확인
    EXPECTED:
      - A1 vs A2:
        - can_melee == True
        - can_ranged == False
      - A1 vs E1:
        - can_melee == False
        - can_ranged == True
    """
    a1 = mk("A1", 1, 5, 5, "ALLY-")
    a2 = mk("A2", 1, 6, 5, "ALLY-")
    e1 = mk("E1", 1, 4, 5, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a1, a2], [e1])

    A1 = CombatantID("A1")
    A2 = CombatantID("A2")
    E1 = CombatantID("E1")

    assert can_melee(bs, A1, A2) is True
    assert can_ranged(bs, A1, A2) is False

    assert can_melee(bs, A1, E1) is False
    assert can_ranged(bs, A1, E1) is True
