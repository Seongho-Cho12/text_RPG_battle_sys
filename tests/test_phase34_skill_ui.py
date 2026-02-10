from __future__ import annotations

import pytest

from battle_system.engine.engine import BattleEngine
from battle_system.core.models import CharacterDef, Stats
from battle_system.core.commands import Skill, Step

from battle_system.app.skill_ui import (
    get_skill_availability,
    compute_input_spec,
    instantiate_skill_with_inputs,
    list_usable_skills,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def mk_char(cid: str, *, level: int = 1, hp: int = 30) -> CharacterDef:
    return CharacterDef(
        cid=cid,
        name=cid,
        level=level,
        stats=Stats(10, 10, 10, 10, 10, 10),
        max_hp=hp,
    )


def mk_battle_1v1() -> tuple[BattleEngine, object]:
    """
    엔진의 turn_order 정렬에 따라 A/B 중 누가 선턴일지 확정하지 않기 위해,
    테스트는 항상 bs.current_actor_id()를 기준으로 돌아가도록 구성한다.
    """
    engine = BattleEngine()
    a = mk_char("A")
    b = mk_char("B")
    bs = engine.create_battle(allies=[a], enemies=[b])
    return engine, bs


def other_of(actor: str) -> str:
    return "B" if actor == "A" else "A"


def mk_attack_skill(
    actor: str,
    *,
    action_type: str = "MAIN",
    cooldown_turns: int = 0,
    target=None,
    range: str = "ANY",
    area: str = "SINGLE",
    crit_stat: str = "STR",
    skill_id: str = "S_ATTACK",
) -> Skill:
    return Skill(
        skill_id=skill_id,
        name="Attack",
        actor=actor,
        action_type=action_type,
        cooldown_turns=cooldown_turns,
        steps=[Step(kind="ATTACK", target=target, range=range, area=area)],
        crit_stat=crit_stat,  # type: ignore[arg-type]
    )


def mk_apply_effect_skill(
    actor: str,
    *,
    action_type: str = "MAIN",
    target=None,
    range: str = "ANY",
    area: str = "SINGLE",
    crit_stat: str = "STR",
    skill_id: str = "S_APPLY",
) -> Skill:
    return Skill(
        skill_id=skill_id,
        name="ApplyEffect",
        actor=actor,
        action_type=action_type,
        cooldown_turns=0,
        steps=[
            Step(
                kind="APPLY_EFFECT",
                target=target,
                range=range,
                area=area,
                effect_id="BLEEDING",
                effect_duration=1,
                status_inflict=0,
            )
        ],
        crit_stat=crit_stat,  # type: ignore[arg-type]
    )


def mk_move_skill(actor: str, *, step_count: int = 1, skill_id: str = "S_MOVE") -> Skill:
    steps = [Step(kind="MOVE_ENGAGE", target=None)]
    if step_count >= 2:
        steps.append(Step(kind="MOVE_DISENGAGE"))
    return Skill(
        skill_id=skill_id,
        name="Move",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        steps=steps,
        crit_stat="STR",  # type: ignore[arg-type]
    )


# -----------------------------------------------------------------------------
# Core availability checks (turn/slot/cooldown/down)
# -----------------------------------------------------------------------------

def test_availability_not_my_turn() -> None:
    """현재 턴 액터가 아닌 캐릭터의 스킬은 UI 선판정에서 NOT_MY_TURN으로 사용 불가여야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    other = other_of(actor)

    sk = mk_attack_skill(other, target=actor)
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "NOT_MY_TURN"


def test_availability_actor_down() -> None:
    """현재 턴 액터가 DOWN 상태면 어떤 스킬도 ACTOR_DOWN으로 사용 불가여야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].hp = 0
    sk = mk_attack_skill(actor, target=enemy)
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "ACTOR_DOWN"


def test_availability_no_action_slot_main() -> None:
    """MAIN 슬롯이 이미 소모(can_main=False)되었으면 MAIN 스킬은 NO_ACTION_SLOT으로 사용 불가여야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].can_main = False
    sk = mk_attack_skill(actor, action_type="MAIN", target=enemy)
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "NO_ACTION_SLOT"


def test_availability_no_action_slot_sub() -> None:
    """SUB 슬롯이 이미 소모(can_sub=False)되었으면 SUB 스킬은 NO_ACTION_SLOT으로 사용 불가여야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].can_sub = False
    sk = mk_attack_skill(actor, action_type="SUB", target=enemy, skill_id="S_SUB")
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "NO_ACTION_SLOT"


def test_availability_on_cooldown_only_when_cooldown_turns_gt_0() -> None:
    """
    cooldown_turns>0인 스킬은 cooldowns[skill_id]>0이면 ON_COOLDOWN으로 사용 불가여야 한다.
    반대로 cooldown_turns==0이면 cooldowns dict에 값이 있어도 UI 선판정은 OK여야 한다(엔진과 동일).
    """
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    sk = mk_attack_skill(actor, cooldown_turns=1, target=enemy, skill_id="S_CD")
    bs.combatants[actor].cooldowns[sk.skill_id] = 3
    av = get_skill_availability(bs, sk)
    assert av.usable is False
    assert av.reason == "ON_COOLDOWN"

    sk2 = mk_attack_skill(actor, cooldown_turns=0, target=enemy, skill_id="S_NOCD")
    bs.combatants[actor].cooldowns[sk2.skill_id] = 99
    av2 = get_skill_availability(bs, sk2)
    assert av2.usable is True
    assert av2.reason == "OK"


# -----------------------------------------------------------------------------
# Status-based blocking: must match engine messages exactly
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "effect_id, expected_msg",
    [
        ("STUN", "STUN: action blocked"),
        ("PARALYSIS", "PARALYSIS: action blocked"),
        ("FROZEN", "FROZEN: action blocked"),
    ],
)
def test_blocked_by_effect_hard_stops(effect_id: str, expected_msg: str) -> None:
    """STUN/PARALYSIS/FROZEN이 있으면 어떤 스킬이든 BLOCKED_BY_EFFECT이며 엔진 메시지까지 일치해야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].effects[effect_id] = 5
    sk = mk_attack_skill(actor, target=enemy)
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "BLOCKED_BY_EFFECT"
    assert av.engine_message == expected_msg


def test_blocked_by_effect_bind_blocks_move_skills_only() -> None:
    """BIND가 있으면 MOVE 포함 스킬만 막히고, MOVE가 없는 스킬은 허용되어야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].effects["BIND"] = 5

    move = mk_move_skill(actor, step_count=1)
    av_move = get_skill_availability(bs, move)
    assert av_move.usable is False
    assert av_move.reason == "BLOCKED_BY_EFFECT"
    assert av_move.engine_message == "BIND: move skill blocked"

    atk = mk_attack_skill(actor, target=enemy)
    av_atk = get_skill_availability(bs, atk)
    assert av_atk.usable is True
    assert av_atk.reason == "OK"


def test_blocked_by_effect_oblivion_blocks_multistep_only() -> None:
    """OBLIVION이 있으면 steps 길이가 2 이상인 스킬은 막히고, 1-step 스킬은 허용되어야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].effects["OBLIVION"] = 5

    one = mk_attack_skill(actor, target=enemy, skill_id="S_ONE")
    av_one = get_skill_availability(bs, one)
    assert av_one.usable is True
    assert av_one.reason == "OK"

    two = Skill(
        skill_id="S_TWO",
        name="TwoStep",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(kind="ATTACK", target=enemy, range="ANY", area="SINGLE"),
            Step(kind="APPLY_HP_DELTA", target=actor, range="ANY", area="SINGLE", hp_delta=1),
        ],
        crit_stat="STR",  # type: ignore[arg-type]
    )
    av_two = get_skill_availability(bs, two)
    assert av_two.usable is False
    assert av_two.reason == "BLOCKED_BY_EFFECT"
    assert av_two.engine_message == "OBLIVION: multi-step skill blocked"


def test_blocked_by_effect_curse_blocks_magic_or_apply_effect() -> None:
    """CURSE가 있으면 (마법 스킬: crit_stat INT/WIS) 또는 APPLY_EFFECT 포함 스킬은 막혀야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[actor].effects["CURSE"] = 5

    magic = mk_attack_skill(actor, target=enemy, crit_stat="INT", skill_id="S_MAGIC")
    av_magic = get_skill_availability(bs, magic)
    assert av_magic.usable is False
    assert av_magic.reason == "BLOCKED_BY_EFFECT"
    assert av_magic.engine_message == "CURSE: magic or apply_effect skill blocked"

    apply = mk_apply_effect_skill(actor, target=enemy, crit_stat="STR", skill_id="S_APPLY2")
    av_apply = get_skill_availability(bs, apply)
    assert av_apply.usable is False
    assert av_apply.reason == "BLOCKED_BY_EFFECT"
    assert av_apply.engine_message == "CURSE: magic or apply_effect skill blocked"

    phys = mk_attack_skill(actor, target=enemy, crit_stat="STR", skill_id="S_PHYS")
    av_phys = get_skill_availability(bs, phys)
    assert av_phys.usable is True
    assert av_phys.reason == "OK"


# -----------------------------------------------------------------------------
# Input spec & candidate generation
# -----------------------------------------------------------------------------

def test_input_spec_target_required_when_step_target_is_none() -> None:
    """Step.target=None이고 area!=ALL인 step이 있으면 target_required=True이고 후보 리스트가 생성되어야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()

    sk = mk_attack_skill(actor, target=None, range="ANY", area="SINGLE", skill_id="S_NEED_T")
    spec = compute_input_spec(bs, actor, sk)

    assert spec.target_required is True
    assert actor not in spec.target_candidates
    assert len(spec.target_candidates) >= 1


def test_target_candidates_melee_vs_ranged() -> None:
    """
    1v1 기본 전투에서:
    - MELEE 후보는 (자기 자신 제외) 같은 그룹에 대상이 없으므로 0개가 되어 NO_VALID_TARGET이어야 한다.
    - RANGED/ANY는 상대가 다른 그룹이므로 후보에 상대가 포함되어 OK여야 한다.
    """
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    sk_melee = mk_attack_skill(actor, target=None, range="MELEE", area="SINGLE", skill_id="S_MELEE")
    av_melee = get_skill_availability(bs, sk_melee)
    assert av_melee.usable is False
    assert av_melee.reason == "NO_VALID_TARGET"

    sk_ranged = mk_attack_skill(actor, target=None, range="RANGED", area="SINGLE", skill_id="S_RANGED")
    av_ranged = get_skill_availability(bs, sk_ranged)
    assert av_ranged.usable is True
    assert enemy in av_ranged.spec.target_candidates

    sk_any = mk_attack_skill(actor, target=None, range="ANY", area="SINGLE", skill_id="S_ANY")
    av_any = get_skill_availability(bs, sk_any)
    assert av_any.usable is True
    assert enemy in av_any.spec.target_candidates


def test_no_valid_target_when_all_others_down() -> None:
    """타겟 후보가 모두 DOWN이면 target_required 스킬은 NO_VALID_TARGET으로 사용 불가여야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    bs.combatants[enemy].hp = 0
    sk = mk_attack_skill(actor, target=None, range="ANY", area="SINGLE", skill_id="S_ANY2")
    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "NO_VALID_TARGET"


def test_out_of_range_all_area_with_no_anchor_and_melee_or_ranged() -> None:
    """
    area=ALL인데 target(None)이며 range가 MELEE/RANGED인 step은
    엔진의 range 체크에서 anchor 부재로 문제가 생길 수 있으므로 UI에서 OUT_OF_RANGE로 선차단해야 한다.
    """
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()

    sk_melee_all = mk_attack_skill(actor, target=None, range="MELEE", area="ALL", skill_id="S_M_ALL")
    av1 = get_skill_availability(bs, sk_melee_all)
    assert av1.usable is False
    assert av1.reason == "OUT_OF_RANGE"

    sk_ranged_all = mk_attack_skill(actor, target=None, range="RANGED", area="ALL", skill_id="S_R_ALL")
    av2 = get_skill_availability(bs, sk_ranged_all)
    assert av2.usable is False
    assert av2.reason == "OUT_OF_RANGE"

    sk_any_all = mk_attack_skill(actor, target=None, range="ANY", area="ALL", skill_id="S_A_ALL")
    av3 = get_skill_availability(bs, sk_any_all)
    assert av3.usable is True
    assert av3.spec.target_required is False


def test_no_throwable_item_when_tactical_throw_requires_item() -> None:
    """TACTICAL_THROW가 포함된 스킬은 아이템 후보가 없으면 NO_THROWABLE_ITEM으로 사용 불가여야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()

    sk = Skill(
        skill_id="S_THROW",
        name="Throw",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[Step(kind="TACTICAL_THROW", target=None, range="ANY", area="SINGLE")],
        crit_stat="STR",  # type: ignore[arg-type]
    )

    av = get_skill_availability(bs, sk)

    assert av.usable is False
    assert av.reason == "NO_THROWABLE_ITEM"
    assert av.spec.item_required is True
    assert av.spec.item_candidates == []


# -----------------------------------------------------------------------------
# instantiate_skill_with_inputs correctness
# -----------------------------------------------------------------------------

def test_instantiate_skill_with_inputs_fills_target_and_throw_item() -> None:
    """instantiate_skill_with_inputs는 필요한 입력을 step 필드에 채운 새 Skill 인스턴스를 만들어야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    sk = Skill(
        skill_id="S_MIX",
        name="Mix",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[
            Step(kind="ATTACK", target=None, range="ANY", area="SINGLE"),
            Step(kind="TACTICAL_THROW", target=None, range="ANY", area="SINGLE"),
        ],
        crit_stat="STR",  # type: ignore[arg-type]
    )

    concrete = instantiate_skill_with_inputs(sk, target=enemy, throw_item_id="STONE")

    assert concrete.steps[0].target == enemy
    assert concrete.steps[1].throw_item_id == "STONE"


def test_instantiate_skill_with_inputs_raises_when_missing_required_inputs() -> None:
    """필수 입력(target 또는 throw_item_id)이 누락되면 instantiate_skill_with_inputs는 예외를 발생시켜야 한다."""
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    sk_need_target = Skill(
        skill_id="S_NEED_T",
        name="NeedTarget",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[Step(kind="ATTACK", target=None, range="ANY", area="SINGLE")],
        crit_stat="STR",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        instantiate_skill_with_inputs(sk_need_target, target=None)

    sk_need_item = Skill(
        skill_id="S_NEED_I",
        name="NeedItem",
        actor=actor,
        action_type="MAIN",
        cooldown_turns=0,
        steps=[Step(kind="TACTICAL_THROW", target=enemy, range="ANY", area="SINGLE")],
        crit_stat="STR",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        instantiate_skill_with_inputs(sk_need_item, throw_item_id=None)


# -----------------------------------------------------------------------------
# list_usable_skills behavior
# -----------------------------------------------------------------------------

def test_list_usable_skills_filters_by_actor_action_type_and_usability() -> None:
    """
    list_usable_skills는
    - actor 일치
    - action_type 일치
    - get_skill_availability에서 usable=True
    인 스킬만 반환해야 한다(쿨다운 등으로 unusable이면 제외).
    """
    _, bs = mk_battle_1v1()
    actor = bs.current_actor_id()
    enemy = other_of(actor)

    s_main_ok = mk_attack_skill(actor, action_type="MAIN", target=enemy, skill_id="S_MAIN_OK")
    s_sub_ok = mk_attack_skill(actor, action_type="SUB", target=enemy, skill_id="S_SUB_OK")
    s_other_actor = mk_attack_skill(enemy, action_type="MAIN", target=actor, skill_id="S_OTHER_ACTOR")

    # MAIN이지만 쿨다운으로 unusable
    s_main_cd = mk_attack_skill(actor, action_type="MAIN", target=enemy, cooldown_turns=1, skill_id="S_MAIN_CD")
    bs.combatants[actor].cooldowns[s_main_cd.skill_id] = 2

    skills = [s_main_ok, s_sub_ok, s_other_actor, s_main_cd]

    mains = list_usable_skills(bs, actor=actor, skills=skills, action_type="MAIN")
    main_ids = [sk.skill_id for sk, _ in mains]
    assert main_ids == ["S_MAIN_OK"]

    subs = list_usable_skills(bs, actor=actor, skills=skills, action_type="SUB")
    sub_ids = [sk.skill_id for sk, _ in subs]
    assert sub_ids == ["S_SUB_OK"]
