import pytest

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import BattleState, CharacterDef, CombatantState, Stats, ItemDef
from battle_system.core.commands import Skill, Step
from battle_system.engine.engine import BattleEngine
import battle_system.engine.engine as engine_mod


def _mk_bs():
    a = CombatantID("A")
    b = CombatantID("B")

    defs = {
        a: CharacterDef(
            cid=a,
            name="A",
            level=1,
            stats=Stats(str=16, agi=5, con=5, int=5, wis=5, cha=5),
            max_hp=50,
            basic_attack_range="MELEE",
        ),
        b: CharacterDef(
            cid=b,
            name="B",
            level=1,
            stats=Stats(str=0, agi=0, con=0, int=0, wis=0, cha=0),
            max_hp=50,
            basic_attack_range="MELEE",
        ),
    }

    g1 = GroupID(1)
    g2 = GroupID(2)

    combatants = {
        a: CombatantState(cid=a, team="ALLY", max_hp=50, _hp=50, group_id=g1),
        b: CombatantState(cid=b, team="ENEMY", max_hp=50, _hp=50, group_id=g2),
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


def test_phase31_throw_requires_item_id_and_does_nothing():
    bs, a, b = _mk_bs()

    # 아이템/인벤 준비
    bs.items["ITEM_ROCK"] = ItemDef(item_id="ITEM_ROCK", weight=8)
    bs.inventory_snapshot[a] = {"ITEM_ROCK": 1}

    eng = BattleEngine()

    skill = Skill(
        skill_id="SK_THROW",
        name="Throw",
        actor=a,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(
                kind="TACTICAL_THROW",
                target=b,
                range="RANGED",
                area="SINGLE",
                throw_item_id=None,  # 핵심: 선택 안 함
            )
        ],
        crit_stat="STR",
    )

    out = eng.apply_skill(bs, skill)

    # 스냅샷/델타 변화 없음
    assert bs.inventory_snapshot[a]["ITEM_ROCK"] == 1
    assert bs.inventory_delta.get(a) is None or bs.inventory_delta[a] == {}
    # 로그에 missing이 찍히는지만 확인(문구는 바뀔 수 있으니 포함 여부만)
    assert any("missing throw_item_id" in e for e in out.events)


def test_phase31_throw_does_not_consume_if_out_of_range():
    """
    사거리/타겟 검증 실패하면 아이템이 소비되면 안 됨.
    (현재 코드 순서면 여기서 소비되는 버그가 발생)
    """
    bs, a, b = _mk_bs()

    # 같은 그룹으로 만들어서 RANGED 조건을 일부러 깨기 (RANGED는 group 다를 때만 True)
    bs.combatants[b].group_id = bs.combatants[a].group_id
    bs.groups[bs.combatants[a].group_id] = [a, b]

    bs.items["ITEM_ROCK"] = ItemDef(item_id="ITEM_ROCK", weight=8)
    bs.inventory_snapshot[a] = {"ITEM_ROCK": 1}

    eng = BattleEngine()

    skill = Skill(
        skill_id="SK_THROW",
        name="Throw",
        actor=a,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(
                kind="TACTICAL_THROW",
                target=b,
                range="RANGED",
                area="SINGLE",
                throw_item_id="ITEM_ROCK",
            )
        ],
        crit_stat="STR",
    )

    out = eng.apply_skill(bs, skill)

    # 실패했으므로 소비되면 안 됨
    assert bs.inventory_snapshot[a]["ITEM_ROCK"] == 1
    assert bs.inventory_delta.get(a) is None or bs.inventory_delta[a] == {}
    assert any("OUT_OF_RANGE" in e for e in out.events)


def test_phase31_throw_consumes_records_delta_applies_mods_breaks_stealth_and_allows_chain(monkeypatch):
    """
    - 투척 성공 시 snapshot 감소 + delta -1
    - 무게(8) -> hit(-10+STR//4)=-6, weak=-10, critical=+2 적용
    - hit 성공 시 STEALTH 해제
    - throw 결과가 CRITICAL(=3)로 계산돼 다음 step(require_prev_gte=3)이 실행됨
    """
    bs, a, b = _mk_bs()

    bs.items["ITEM_ROCK"] = ItemDef(item_id="ITEM_ROCK", weight=8)
    bs.inventory_snapshot[a] = {"ITEM_ROCK": 1}

    # 은신 상태 부여(투척 hit 시 깨져야 함)
    bs.combatants[a].effects["STEALTH"] = 999

    captured = {}

    def fake_basic_attack(bs_, attacker, defender, *, modifiers, crit_stat):
        captured["modifiers"] = modifiers
        captured["crit_stat"] = crit_stat
        # hit 성공 + outcome CRITICAL 로 강제
        return {"hit": True, "outcome": "CRITICAL", "damage": 9}

    monkeypatch.setattr(engine_mod, "basic_attack", fake_basic_attack)

    eng = BattleEngine()

    skill = Skill(
        skill_id="SK_THROW_CHAIN",
        name="Throw+Chain",
        actor=a,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(
                kind="TACTICAL_THROW",
                target=b,
                range="RANGED",
                area="SINGLE",
                throw_item_id="ITEM_ROCK",
            ),
            Step(
                kind="APPLY_EFFECT",
                target=b,
                range="ANY",
                area="SINGLE",
                effect_id="BLEEDING",
                effect_duration=1,
                status_inflict=10,
                require_prev_gte=3,  # throw가 CRITICAL(3)로 평가되어야 실행됨
            ),
        ],
        crit_stat="STR",
    )

    out = eng.apply_skill(bs, skill)

    # 인벤 소비 + 델타 기록
    assert "ITEM_ROCK" not in bs.inventory_snapshot[a]
    assert bs.inventory_delta[a]["ITEM_ROCK"] == -1

    # 무게 8, STR=16 => STR//4=4 => hit=-10+4=-6
    mods = captured["modifiers"]
    assert mods.hit == -6
    assert mods.weak == -10
    assert mods.critical == 2
    assert captured["crit_stat"] == "STR"

    # 은신 해제
    assert "STEALTH" not in bs.combatants[a].effects

    # 체인 step 실행 결과: BLEEDING이 적용되어 있어야 함
    assert bs.combatants[b].effects.get("BLEEDING", 0) > 0

    # 참고용: 이벤트에 THROW_CONSUME가 찍혔는지
    assert any("THROW_CONSUME" in e for e in out.events)
