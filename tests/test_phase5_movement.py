import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.engine.engine import BattleEngine
from battle_system.formation.groups import same_group
from battle_system.formation.movement import engage, disengage


def mk(cid: str, lvl: int, agi: int, wis: int, team_hint: str = "") -> CharacterDef:
    """
    TITLE: Phase 5 이동 테스트용 캐릭터 정의 생성 헬퍼
    SETUP:
      - cid/name/level/Stats/max_hp/basic_attack_range를 채운 CharacterDef를 만든다.
      - 본 Phase에서는 그룹 이동(ENGAGE/DISENGAGE)만 검증하므로 스킬/장비는 사용하지 않는다.
    EXPECTED:
      - 반환된 CharacterDef는 BattleEngine.create_battle의 입력으로 사용 가능하다.
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=lvl,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=10,
        basic_attack_range="MELEE",
    )


def _all_members(bs):
    """
    TITLE: 현재 battle_state.groups의 전체 멤버를 평탄화하여 리스트로 반환(중복 검증용)
    EXPECTED:
      - 모든 그룹의 member를 하나로 합친 리스트 반환
    """
    out = []
    for gid, members in bs.groups.items():
        out.extend(members)
    return out


def test_phase5_engage_moves_actor_into_target_group_and_updates_membership():
    """
    TITLE: ENGAGE가 대상(target)의 그룹으로 합류하며, group_id와 groups 멤버십이 일관되게 갱신되는지 검증
    SETUP:
      - Allies:
        - A1 (lv1, agi=6, wis=5)
        - A2 (lv1, agi=5, wis=5)
      - Enemies:
        - E1 (lv1, agi=4, wis=5)
      - 초기 상태:
        - A1/A2는 같은 그룹(아군 그룹)
        - E1은 적군 그룹
      - 이동 규칙(ENGAGE):
        - actor는 target이 속한 그룹으로 합류한다.
    STEPS:
      1) create_battle([A1,A2],[E1])
      2) ENGAGE: engage(bs, actor=A1, target=E1) 호출
      3) A1이 E1과 same_group이 되었는지 확인
      4) A2는 여전히 A1과 같은 그룹이 아닌지(즉 A2는 아군 그룹에 남았는지) 확인
      5) groups 멤버십이 중복/누락 없이 정확한지 확인
    EXPECTED:
      - A1.group_id == E1.group_id
      - same_group(A1,E1) == True
      - same_group(A1,A2) == False  (A1이 적군 그룹으로 이동했으므로)
      - 전체 멤버십에 A1/A2/E1이 각각 정확히 1회씩 존재
    """
    a1 = mk("A1", 1, 6, 5, "ALLY-")
    a2 = mk("A2", 1, 5, 5, "ALLY-")
    e1 = mk("E1", 1, 4, 5, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a1, a2], [e1])

    A1 = CombatantID("A1")
    A2 = CombatantID("A2")
    E1 = CombatantID("E1")

    assert same_group(bs, A1, A2) is True
    assert same_group(bs, A1, E1) is False

    engage(bs, actor=A1, target=E1)

    assert same_group(bs, A1, E1) is True
    assert same_group(bs, A1, A2) is False

    flat = _all_members(bs)
    assert flat.count(A1) == 1
    assert flat.count(A2) == 1
    assert flat.count(E1) == 1


def test_phase5_disengage_creates_new_solo_group_and_removes_empty_group():
    """
    TITLE: DISENGAGE가 actor 단독 그룹을 생성하고, 기존 그룹이 비면 제거되는지 검증
    SETUP:
      - Allies:
        - A1 (lv1, agi=6, wis=5)
        - A2 (lv1, agi=5, wis=5)
      - Enemies:
        - E1 (lv1, agi=4, wis=5)
      - 초기 상태:
        - A1/A2는 같은 그룹(아군 그룹)
    STEPS:
      1) create_battle([A1,A2],[E1])
      2) DISENGAGE: new_gid = disengage(bs, actor=A1)
      3) A1이 A2와 다른 그룹이 되었는지 확인
      4) 새 그룹(new_gid)의 멤버가 A1 단독인지 확인
      5) 전체 멤버십이 중복/누락 없이 정확한지 확인
    EXPECTED:
      - same_group(A1,A2) == False
      - bs.groups[new_gid] == [A1]
      - A2는 기존 아군 그룹에 남아있음
      - 모든 멤버(A1,A2,E1)가 정확히 1회씩만 존재
    """
    a1 = mk("A1", 1, 6, 5, "ALLY-")
    a2 = mk("A2", 1, 5, 5, "ALLY-")
    e1 = mk("E1", 1, 4, 5, "ENEMY-")

    eng = BattleEngine()
    bs = eng.create_battle([a1, a2], [e1])

    A1 = CombatantID("A1")
    A2 = CombatantID("A2")
    E1 = CombatantID("E1")

    assert same_group(bs, A1, A2) is True

    new_gid = disengage(bs, actor=A1)

    assert same_group(bs, A1, A2) is False
    assert list(bs.groups[new_gid]) == [A1]

    flat = _all_members(bs)
    assert flat.count(A1) == 1
    assert flat.count(A2) == 1
    assert flat.count(E1) == 1


def test_phase5_engage_same_group_is_noop():
    """
    TITLE: ENGAGE가 이미 같은 그룹인 경우 no-op으로 처리되는지 검증
    SETUP:
      - Allies: A1, A2 (같은 그룹)
    STEPS:
      1) create_battle([A1,A2],[])
      2) engage(bs, actor=A1, target=A2)
    EXPECTED:
      - A1과 A2는 여전히 같은 그룹
      - 그룹 멤버십에 변화 없음
    """
    a1 = mk("A1", 1, 6, 5, "ALLY-")
    a2 = mk("A2", 1, 5, 5, "ALLY-")

    eng = BattleEngine()
    bs = eng.create_battle([a1, a2], [])

    A1 = CombatantID("A1")
    A2 = CombatantID("A2")

    before_groups = {gid: list(m) for gid, m in bs.groups.items()}

    engage(bs, actor=A1, target=A2)

    assert same_group(bs, A1, A2) is True
    assert before_groups == bs.groups
