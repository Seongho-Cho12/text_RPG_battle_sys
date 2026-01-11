import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.engine.engine import BattleEngine
from battle_system.formation.movement import engage, disengage
from battle_system.formation.reactions import reaction_attack_candidates


def mk(cid: str, lvl: int, agi: int, wis: int, team_hint: str = "", atk_range="MELEE") -> CharacterDef:
    """
    TITLE: Phase 6 반응공격 테스트용 캐릭터 정의 생성 헬퍼
    SETUP:
      - cid/name/level/Stats/max_hp/basic_attack_range를 채운 CharacterDef 생성
      - atk_range로 근접(MELEE)/원거리(RANGED) 구분
    EXPECTED:
      - 반환된 CharacterDef는 반응공격 후보 판정에 사용 가능
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=f"{team_hint}{cid}",
        level=lvl,
        stats=Stats(str=10, agi=agi, con=10, int=10, wis=wis, cha=10),
        max_hp=10,
        basic_attack_range=atk_range,
    )


def test_phase6_reaction_candidates_basic_melee_enemies():
    """
    TITLE: 이동 직전 같은 그룹의 근접 적 전원이 반응공격 후보로 산출되는지 검증
    SETUP:
      - Allies:
        - A1 (근접)
      - Enemies (같은 그룹에 붙어 있는 상황 가정):
        - E1 (근접)
        - E2 (근접)
        - E3 (원거리)
      - 초기 상태:
        - A1/E1/E2/E3 모두 같은 그룹
    STEPS:
      1) create_battle([A1],[E1,E2,E3])
      2) 이동 직전 group_id를 prev_gid로 저장
      3) A1이 DISENGAGE로 그룹 이탈
      4) reaction_attack_candidates(bs, mover=A1, prev_group_id=prev_gid, reaction_immune=False)
    EXPECTED:
      - 근접 적 E1, E2만 후보
      - 원거리 적 E3는 제외
    """
    a1 = mk("A1", 1, 6, 5, "ALLY-", atk_range="MELEE")
    e1 = mk("E1", 1, 4, 5, "ENEMY-", atk_range="MELEE")
    e2 = mk("E2", 1, 3, 5, "ENEMY-", atk_range="MELEE")
    e3 = mk("E3", 1, 2, 5, "ENEMY-", atk_range="RANGED")

    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1, e2, e3])

    A1 = CombatantID("A1")
    E1 = CombatantID("E1")

    engage(bs, actor=A1, target=E1)

    prev_gid = bs.combatants[A1].group_id
    disengage(bs, actor=A1)

    cands = reaction_attack_candidates(
        bs,
        mover=A1,
        prev_group_id=prev_gid,
        reaction_immune=False,
    )

    assert set(cands) == {CombatantID("E1"), CombatantID("E2")}


def test_phase6_reaction_immune_movement_returns_empty():
    """
    TITLE: 반응공격 면제 이동인 경우 후보가 항상 빈 리스트인지 검증
    SETUP:
      - Allies:
        - A1
      - Enemies:
        - E1 (근접)
      - 초기 상태:
        - A1/E1 같은 그룹
    STEPS:
      1) create_battle([A1],[E1])
      2) prev_gid 저장
      3) A1 DISENGAGE
      4) reaction_attack_candidates(..., reaction_immune=True)
    EXPECTED:
      - 후보 리스트는 항상 빈 리스트
    """
    a1 = mk("A1", 1, 6, 5, "ALLY-", atk_range="MELEE")
    e1 = mk("E1", 1, 4, 5, "ENEMY-", atk_range="MELEE")

    eng = BattleEngine()
    bs = eng.create_battle([a1], [e1])

    A1 = CombatantID("A1")

    prev_gid = bs.combatants[A1].group_id
    disengage(bs, actor=A1)

    cands = reaction_attack_candidates(
        bs,
        mover=A1,
        prev_group_id=prev_gid,
        reaction_immune=True,
    )

    assert cands == []


def test_phase6_reaction_excludes_same_team():
    """
    TITLE: 이동 직전 같은 그룹에 있더라도 같은 팀은 반응공격 후보에서 제외되는지 검증
    SETUP:
      - Allies:
        - A1 (이동자)
        - A2 (근접, 같은 팀)
      - Enemies: 없음
      - 초기 상태:
        - A1/A2 같은 그룹
    STEPS:
      1) create_battle([A1,A2],[])
      2) prev_gid 저장
      3) A1 DISENGAGE
      4) reaction_attack_candidates(..., reaction_immune=False)
    EXPECTED:
      - 같은 팀(A2)은 제외 → 후보는 빈 리스트
    """
    a1 = mk("A1", 1, 6, 5, "ALLY-", atk_range="MELEE")
    a2 = mk("A2", 1, 5, 5, "ALLY-", atk_range="MELEE")

    eng = BattleEngine()
    bs = eng.create_battle([a1, a2], [])

    A1 = CombatantID("A1")

    prev_gid = bs.combatants[A1].group_id
    disengage(bs, actor=A1)

    cands = reaction_attack_candidates(
        bs,
        mover=A1,
        prev_group_id=prev_gid,
        reaction_immune=False,
    )

    assert cands == []
