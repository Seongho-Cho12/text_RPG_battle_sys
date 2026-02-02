from __future__ import annotations

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import CharacterDef, Stats
from battle_system.core.types import CombatantID
from battle_system.core.commands import Skill, Step
from battle_system.rules.indices.facade import IndexModifiers


# -------------------------
# fixtures
# -------------------------

@pytest.fixture
def engine_1v1():
    """
    1v1 전투 픽스처
    반환: (engine, battle_state, A_id, E_id)
    """
    eng = BattleEngine()

    A = CharacterDef(
        cid=CombatantID("A"),
        name="A",
        level=10,
        stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10),
        max_hp=50,
    )
    E = CharacterDef(
        cid=CombatantID("E"),
        name="E",
        level=10,
        stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=10),
        max_hp=50,
    )

    bs = eng.create_battle([A], [E])
    return eng, bs, CombatantID("A"), CombatantID("E")


def _advance_to_actor(eng: BattleEngine, bs, actor: CombatantID) -> None:
    """
    원하는 actor 턴이 될 때까지 end_turn 반복.
    (항상 최소 1회는 턴을 넘긴 뒤 검사)
    """
    for _ in range(3):
        eng.end_turn(bs)
        if bs.current_actor_id() == actor:
            return
    raise AssertionError("failed to advance to desired actor")


# -------------------------
# skill builders
# -------------------------

def _mk_instant_death_skill(actor: CombatantID, target: CombatantID, action_type: str = "MAIN") -> Skill:
    return Skill(
        skill_id="T_INSTANT_DEATH",
        name="instant_death",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=target,
                range="ANY",
                area="SINGLE",
                effect_id="INSTANT_DEATH",
                effect_duration=5,   # 충분히 크게(테스트 안정)
                status_inflict=9999, # 거의 확정 성공
            )
        ],
    )


def _mk_attack_skill_with_step_mods(
    actor: CombatantID,
    target: CombatantID,
    *,
    hit_delta: int,
    weak_delta: int,
    action_type: str = "MAIN",
) -> Skill:
    return Skill(
        skill_id="T_ATTACK_WITH_STEP_MODS",
        name="attack_with_step_mods",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="ATTACK",
                target=target,
                range="ANY",
                area="SINGLE",
                # ✅ 이번 Phase에서 추가한 Step 필드
                attack_modifiers=IndexModifiers(
                    hit=hit_delta,
                    evade=0,
                    weak=weak_delta,
                    strong=0,
                    critical=0,
                ),
            )
        ],
    )


def _mk_plain_attack_skill(actor: CombatantID, target: CombatantID, action_type: str = "MAIN") -> Skill:
    return Skill(
        skill_id="T_PLAIN_ATTACK",
        name="plain_attack",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        crit_stat="STR",
        steps=[
            Step(
                kind="ATTACK",
                target=target,
                range="ANY",
                area="SINGLE",
                # attack_modifiers 기본값(IndexModifiers())이 들어가야 함
            )
        ],
    )


# -------------------------
# tests
# -------------------------

def test_phase29_instant_death_applies_immediately(engine_1v1):
    """
    [Phase 29] INSTANT_DEATH:
    - APPLY_EFFECT로 INSTANT_DEATH가 적용되면 즉시 hp=0
    - events에 INSTANT_DEATH 로그가 존재해야 한다.
    """
    eng, bs, A, E = engine_1v1

    # A 턴으로 맞추기(현재 턴이 A가 아닐 수 있음)
    if bs.current_actor_id() != A:
        _advance_to_actor(eng, bs, A)

    out = eng.apply_skill(bs, _mk_instant_death_skill(A, E, action_type="MAIN"))

    assert bs.combatants[E].hp == 0
    assert any(ev.startswith("INSTANT_DEATH:") for ev in out.events), "\n".join(out.events)


def test_phase29_step_attack_modifiers_used_for_attack_and_do_not_persist(engine_1v1, monkeypatch):
    """
    [Phase 29] Step.attack_modifiers:
    - (적용) 해당 ATTACK에서 basic_attack에 전달되는 modifiers가 지정값이어야 한다.
    - (비잔존) 다음 공격(plain attack)에서는 modifiers가 0이어야 한다.
    """
    eng, bs, A, E = engine_1v1

    if bs.current_actor_id() != A:
        _advance_to_actor(eng, bs, A)

    # ✅ 엔진 모듈이 실제로 호출하는 basic_attack을 패치
    import battle_system.engine.engine as eng_mod

    real_basic_attack = eng_mod.basic_attack
    seen = []  # (hit, weak)

    def spy_basic_attack(bs_, attacker, defender, *, modifiers, crit_stat):
        seen.append((int(modifiers.hit), int(modifiers.weak)))
        return real_basic_attack(bs_, attacker=attacker, defender=defender, modifiers=modifiers, crit_stat=crit_stat)

    monkeypatch.setattr(eng_mod, "basic_attack", spy_basic_attack)

    # 1) step mods attack
    eng.apply_skill(bs, _mk_attack_skill_with_step_mods(A, E, hit_delta=123, weak_delta=77, action_type="MAIN"))

    # 턴 넘겨 A 턴 재확보
    _advance_to_actor(eng, bs, A)

    # 2) plain attack (mods should be 0)
    eng.apply_skill(bs, _mk_plain_attack_skill(A, E, action_type="MAIN"))

    assert len(seen) >= 2, f"basic_attack calls not captured: {seen}"

    # 첫 공격: step mods 반영
    assert seen[0] == (123, 77)

    # 두 번째 공격: 이전 step mods가 남으면 안 됨
    assert seen[1] == (0, 0)
