from battle_system.core.models import Stats, CharacterDef, CombatantState
from battle_system.core.types import CombatantID, GroupID
from battle_system.engine.engine import BattleEngine
from battle_system.core.commands import Step


def _mk_char(cid: str, *, level: int, stats: Stats, max_hp: int) -> CharacterDef:
    return CharacterDef(cid=CombatantID(cid), name=cid, level=level, stats=stats, max_hp=max_hp)


def test_phase18_down_state_hp_clamp_and_is_down_property_unit():
    """
    TITLE: CombatantState의 HP 클램프 및 is_down 자동 판별(Unit) 검증
    PURPOSE:
      - CombatantState는 hp에 0 이하 값이 들어가면 자동으로 0으로 저장되어야 한다.
      - is_down은 별도 저장/갱신 없이 hp==0인지 여부로 자동 판별되어야 한다.
    SETUP:
      - 초기 생성 시 _hp=-5로 생성 (의도적으로 음수)
      - 이후 hp setter로 음수, 양수를 번갈아 대입
    STEPS:
      1) _hp=-5로 생성 후 hp가 0으로 클램프되는지 확인
      2) hp=-100 대입 후 hp=0 유지 및 is_down=True 확인
      3) hp=7 대입 후 is_down=False로 자동 변경 확인
      4) hp=0 대입 후 is_down=True 확인
    EXPECTED:
      - hp는 절대 음수가 되지 않음
      - is_down은 hp==0일 때 True, 그 외 False
    """
    st = CombatantState(
        cid=CombatantID("T1"),
        team="ALLY",
        group_id=GroupID(0),
        _hp=-5,  # 생성 시 음수
    )

    print("\n[Phase18 Unit] init _hp=-5 -> hp, is_down:", st.hp, st.is_down)
    assert st.hp == 0
    assert st.is_down is True

    st.hp = -100
    print("[Phase18 Unit] set hp=-100 -> hp, is_down:", st.hp, st.is_down)
    assert st.hp == 0
    assert st.is_down is True

    st.hp = 7
    print("[Phase18 Unit] set hp=7 -> hp, is_down:", st.hp, st.is_down)
    assert st.hp == 7
    assert st.is_down is False

    st.hp = 0
    print("[Phase18 Unit] set hp=0 -> hp, is_down:", st.hp, st.is_down)
    assert st.hp == 0
    assert st.is_down is True


def test_phase18_down_state_engine_attack_sets_hp_to_zero_and_down_true():
    """
    TITLE: 엔진/공격 파이프라인에서 HP가 0으로 클램프되고 is_down이 True가 되는지 검증
    PURPOSE:
      - 실제 엔진(apply_steps)로 ATTACK 수행 시,
        피해로 인해 hp가 0 이하가 되면 hp는 0으로 클램프되어야 한다.
      - is_down은 hp==0을 기반으로 자동 True여야 한다.
    SETUP:
      - A1 vs E1 1:1 전투
      - E1 max_hp를 매우 작게(예: 1) 설정하여 한 번의 공격(WEAK=1 등)에도 0이 되게 유도
      - 공격 결과(outcome)는 랜덤일 수 있으므로,
        "최종 HP가 0 이하로 내려갈 수 있는 상황"을 만들기 위해 E1 hp=1로 설정
    STEPS:
      1) 전투 생성
      2) A1이 E1에게 ATTACK step 실행
      3) E1의 hp가 0 또는 1이 될 수 있는데,
         - EVADE면 hp=1 유지, is_down=False
         - 명중이면 hp=0, is_down=True
      4) 명중이 한 번이라도 발생하도록 최대 N번(예: 50번) 반복 시도
    EXPECTED:
      - 어떤 시도에서든 hp가 음수로 내려가는 경우는 없어야 함
      - 명중이 발생한 시점에는 hp==0, is_down=True
    """
    eng = BattleEngine()

    a1 = _mk_char(
        "A1",
        level=10,
        stats=Stats(str=10, agi=10, con=10, int=10, wis=10, cha=0),
        max_hp=50,
    )
    e1 = _mk_char(
        "E1",
        level=1,
        stats=Stats(str=1, agi=1, con=1, int=1, wis=1, cha=0),
        max_hp=1,  # 한 대만 맞아도 0으로
    )

    bs = eng.create_battle([a1], [e1])

    defender = CombatantID("E1")
    hit_observed = False

    print("\n[Phase18 Engine] try attacks until E1 goes DOWN (max 50 trials)")

    for i in range(50):
        # E1을 매번 1로 리셋(시도 반복용)
        bs.combatants[defender].hp = 1
        assert bs.combatants[defender].hp == 1
        assert bs.combatants[defender].is_down is False

        out = eng.apply_steps(
            bs,
            [Step(kind="ATTACK", actor=bs.current_actor_id(), target=defender, action_type="MAIN")],
        )

        hp_after = bs.combatants[defender].hp
        down_after = bs.combatants[defender].is_down

        # hp는 절대 음수면 안 됨
        assert hp_after >= 0

        print(f"  trial={i:02d} -> E1 hp={hp_after}, is_down={down_after}")
        for ev in out.events:
            print("    ", ev)

        if hp_after == 0:
            # 명중 발생(WEAK/STRONG/CRITICAL 중 하나)
            assert down_after is True
            hit_observed = True
            break
        else:
            # EVADE면 hp=1 유지
            assert hp_after == 1
            assert down_after is False

    assert hit_observed, "50회 시도 내에 한 번도 명중이 안 나왔습니다. (확률상 드물지만 가능)"
