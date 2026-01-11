import pytest

from battle_system.core.types import CombatantID
from battle_system.core.models import Stats, CharacterDef
from battle_system.engine.engine import BattleEngine

from battle_system.rules.indices.hit import (
    compute_hit_index,
    compute_evade_index,
    compute_hit_indices,
)
from battle_system.rules.indices.crit import (
    level_to_rarity,
    compute_crit_indices,
)
from battle_system.rules.indices.facade import (
    compute_attack_indices,
    IndexModifiers,
)


def mk_char(cid: str, *, level: int, stats: Stats) -> CharacterDef:
    """
    TITLE: Phase15 테스트용 CharacterDef 생성 헬퍼
    PURPOSE:
      - BattleEngine.create_battle가 요구하는 최소 필드를 채운 CharacterDef를 빠르게 만든다.
      - facade 테스트에서 BattleState를 만들기 위해 사용한다.
    SETUP:
      - cid는 문자열로 받고 내부에서 CombatantID로 변환한다.
      - 기본 공격 사거리는 테스트에 영향이 없으므로 MELEE로 고정한다.
    EXPECTED:
      - 반환된 CharacterDef는 create_battle 입력으로 바로 사용할 수 있다.
    """
    return CharacterDef(
        cid=CombatantID(cid),
        name=cid,
        level=level,
        stats=stats,
        max_hp=50,
        basic_attack_range="MELEE",
    )


def mk_bs_1v1(attacker: CharacterDef, defender: CharacterDef):
    """
    TITLE: Phase15 facade 테스트용 1v1 BattleState 생성
    PURPOSE:
      - compute_attack_indices(bs, attacker, defender)를 호출할 수 있는 BattleState를 만든다.
    SETUP:
      - allies=[attacker], enemies=[defender]로 생성한다.
      - turn order는 내부 로직대로지만, 이 테스트는 turn을 사용하지 않는다.
    EXPECTED:
      - bs.defs에 attacker/defender cid가 존재한다.
    """
    eng = BattleEngine()
    bs = eng.create_battle([attacker], [defender])
    assert attacker.cid in bs.defs
    assert defender.cid in bs.defs
    return bs


def test_phase15_hit_index_formula():
    """
    TITLE: 명중 지수(hit) 공식 검증
    PURPOSE:
      - hit = 40 + level 공식을 정확히 따른다.
    SETUP:
      - 여러 레벨(1, 10, 20)에 대해 기대값을 직접 계산한다.
    STEPS:
      1) compute_hit_index(level)를 호출한다.
      2) 반환값이 40+level과 일치하는지 확인한다.
    EXPECTED:
      - level=1  -> 41
      - level=10 -> 50
      - level=20 -> 60
    """
    assert compute_hit_index(1) == 41
    assert compute_hit_index(10) == 50
    assert compute_hit_index(20) == 60


def test_phase15_evade_index_formula_and_symmetry():
    """
    TITLE: 회피 지수(evade) 공식 및 대칭성 검증
    PURPOSE:
      - evade = {(max(AGI,WIS)*2 + min(AGI,WIS))}/3 공식을 정확히 따른다.
      - AGI/WIS가 서로 바뀌어도 결과는 동일해야 한다(대칭성).
      - 정수화는 현재 구현대로 내림(int)임을 전제로 기대값을 검증한다.
    SETUP:
      - 케이스1: AGI=9, WIS=6 -> (9*2 + 6)/3 = 8
      - 케이스2: AGI=6, WIS=9 -> 동일하게 8
      - 케이스3: AGI=10, WIS=1 -> (10*2 + 1)/3 = 7 (정수 내림)
    STEPS:
      1) compute_evade_index(stats)를 호출한다.
      2) 기대값과 비교한다.
    EXPECTED:
      - 케이스1=8, 케이스2=8, 케이스3=7
    """
    s1 = Stats(str=0, agi=9, con=0, int=0, wis=6, cha=0)
    s2 = Stats(str=0, agi=6, con=0, int=0, wis=9, cha=0)
    s3 = Stats(str=0, agi=10, con=0, int=0, wis=1, cha=0)

    assert compute_evade_index(s1) == 8
    assert compute_evade_index(s2) == 8
    assert compute_evade_index(s3) == 7


def test_phase15_compute_hit_indices_pair():
    """
    TITLE: compute_hit_indices가 hit/evade를 함께 반환하는지 검증
    PURPOSE:
      - attacker_level과 defender_stats를 받아 HitIndices(hit, evade)를 반환한다.
    SETUP:
      - attacker_level=10 -> hit=50
      - defender AGI=9, WIS=6 -> evade=8
    STEPS:
      1) compute_hit_indices(10, stats) 호출
      2) hit/evade 각각 기대값 비교
    EXPECTED:
      - hit=50, evade=8
    """
    stats = Stats(str=0, agi=9, con=0, int=0, wis=6, cha=0)
    he = compute_hit_indices(attacker_level=10, defender_stats=stats)
    assert he.hit == 50
    assert he.evade == 8


def test_phase15_level_to_rarity_mapping_boundaries():
    """
    TITLE: 레벨 -> 희귀도 구간 매핑 경계값 검증
    PURPOSE:
      - 레벨 구간이 요구사항대로 희귀도로 변환되는지 확인한다.
    SETUP:
      - (1,3)=고물
      - (4,8)=일반
      - (9,12)=언커먼
      - (13,16)=레어
      - (17,19)=진귀
      - (20+)=전설
    STEPS:
      - 각 경계 레벨에 대해 level_to_rarity를 호출한다.
    EXPECTED:
      - 경계값이 모두 요구사항과 일치한다.
    """
    assert level_to_rarity(1) == "고물"
    assert level_to_rarity(3) == "고물"
    assert level_to_rarity(4) == "일반"
    assert level_to_rarity(8) == "일반"
    assert level_to_rarity(9) == "언커먼"
    assert level_to_rarity(12) == "언커먼"
    assert level_to_rarity(13) == "레어"
    assert level_to_rarity(16) == "레어"
    assert level_to_rarity(17) == "진귀"
    assert level_to_rarity(19) == "진귀"
    assert level_to_rarity(20) == "전설"
    assert level_to_rarity(25) == "전설"


def test_phase15_crit_indices_strength_like_STR_and_INT():
    """
    TITLE: 치명 지수(STR/INT)가 '근력 무기' 공식을 따르는지 검증
    PURPOSE:
      - STR과 INT는 동일한 공식(근력 무기)이며, primary만 각각 STR/INT로 다르게 들어간다.
    SETUP:
      - level=2 -> 고물
      - STR 케이스: STR=12
        weak  = 20 + (20-12) = 28
        strong= 0 + (12/2)   = 6
        crit  = 0
      - INT 케이스: INT=12 (같은 값이면 STR과 동일한 결과가 나와야 함)
    STEPS:
      1) compute_crit_indices(level, stats, "STR") 호출
      2) compute_crit_indices(level, stats, "INT") 호출
    EXPECTED:
      - STR 결과: (28,6,0)
      - INT 결과: (28,6,0)
    """
    stats = Stats(str=12, agi=0, con=0, int=12, wis=0, cha=0)

    ci_str = compute_crit_indices(attacker_level=2, attacker_stats=stats, crit_stat="STR")
    assert (ci_str.weak, ci_str.strong, ci_str.crit) == (28, 6, 0)

    ci_int = compute_crit_indices(attacker_level=2, attacker_stats=stats, crit_stat="INT")
    assert (ci_int.weak, ci_int.strong, ci_int.crit) == (28, 6, 0)


def test_phase15_crit_indices_agility_like_AGI():
    """
    TITLE: 치명 지수(AGI)가 '민첩 무기' 공식을 따르는지 검증
    PURPOSE:
      - AGI는 민첩 무기 공식으로 계산한다(secondary=STR).
    SETUP:
      - level=10 -> 언커먼
      - AGI=20, STR=10
        weak  = 20 + (25-20) = 25
        strong= 0 + (20*1.5) + (10/2) = 30 + 5 = 35
        crit  = 0 + (20/2) + (10/5) = 10 + 2 = 12
    STEPS:
      - compute_crit_indices(level, stats, "AGI") 호출
    EXPECTED:
      - (weak,strong,crit) == (25,35,12) (정수 내림 전제)
    """
    stats = Stats(str=10, agi=20, con=0, int=0, wis=0, cha=0)
    ci = compute_crit_indices(attacker_level=10, attacker_stats=stats, crit_stat="AGI")
    assert (ci.weak, ci.strong, ci.crit) == (25, 35, 12)


def test_phase15_crit_indices_agility_like_WIS_uses_INT_as_secondary_and_truncates():
    """
    TITLE: 치명 지수(WIS)가 민첩 무기 공식 변형(WIS/INT)으로 계산되고 소수는 내림되는지 검증
    PURPOSE:
      - WIS 타입은 (primary=WIS, secondary=INT)로 민첩 무기 공식을 적용한다.
      - '진귀' 구간에서 crit에 소수가 생기므로 int() 내림이 반영되는지 확인한다.
    SETUP:
      - level=17 -> 진귀
      - WIS=18, INT=14
        weak  = 20 + (20-18) = 22
        strong= 0 + (18*2) + (14/2) = 36 + 7 = 43
        crit  = 0 + (18*1.2) + (14/5) = 21.6 + 2.8 = 24.4 -> int 내림 => 24
    STEPS:
      - compute_crit_indices(level, stats, "WIS") 호출
    EXPECTED:
      - (weak,strong,crit) == (22,43,24)
    """
    stats = Stats(str=0, agi=0, con=0, int=14, wis=18, cha=0)
    ci = compute_crit_indices(attacker_level=17, attacker_stats=stats, crit_stat="WIS")
    assert (ci.weak, ci.strong, ci.crit) == (22, 43, 24)


def test_phase15_facade_compute_attack_indices_applies_modifiers_and_crit_stat():
    """
    TITLE: facade.compute_attack_indices가 (1)hit/evade 공식 (2)crit_stat 선택 (3)modifiers 가산을 모두 반영하는지 검증
    PURPOSE:
      - facade는 외부에서 쓰는 단일 엔트리 포인트이므로 여기서 핵심 연결이 깨지면 전체 전투가 흔들린다.
    SETUP:
      - A1(level=10): hit=50, crit_stat="AGI"로 치명 지수 계산
      - E1 stats(AGI=9, WIS=6): evade=8
      - modifiers: hit +3, evade +2, strong +5
    STEPS:
      1) 1v1 BattleState 생성
      2) compute_attack_indices(bs, A1, E1, crit_stat="AGI", modifiers=...) 호출
      3) hit/evade/crit 값이 (base + mod) 형태인지 확인
    EXPECTED:
      - hit: base 50 + 3 = 53
      - evade: base 8 + 2 = 10
      - crit(AGI, level=10 언커먼, AGI=20 STR=10):
          base weak=25, strong=35, crit=12
        modifiers strong +5 -> strong=40
      - 따라서 crit=(25,40,12)
    """
    A1 = mk_char(
        "A1",
        level=10,
        stats=Stats(str=10, agi=20, con=0, int=0, wis=0, cha=0),
    )
    E1 = mk_char(
        "E1",
        level=1,
        stats=Stats(str=0, agi=9, con=0, int=0, wis=6, cha=0),
    )
    bs = mk_bs_1v1(A1, E1)

    mods = IndexModifiers(hit=3, evade=2, strong=5)
    out = compute_attack_indices(
        bs,
        attacker=CombatantID("A1"),
        defender=CombatantID("E1"),
        crit_stat="AGI",
        modifiers=mods,
    )

    # hit/evade
    assert out.hit_eva.hit == 53
    assert out.hit_eva.evade == 10

    # crit
    assert out.crit.weak == 25
    assert out.crit.strong == 40
    assert out.crit.critical == 12

    print(
        "\n[Phase15 Facade] hit/evade/crit:",
        (out.hit_eva.hit, out.hit_eva.evade, out.crit.weak, out.crit.strong, out.crit.critical),
    )
