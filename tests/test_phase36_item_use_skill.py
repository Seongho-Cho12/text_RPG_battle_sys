"""
Phase 36 — 아이템 use_skill 스키마 확장 테스트

- use_skill 있는 아이템 / 없는 아이템 로드 구분
- use_skill 내부 steps 파싱 정확성
- weight 제한 위반 시 예외 발생
- UseSkillDef 필드 기본값 확인
"""
from __future__ import annotations

import os
import shutil
import pytest
from pathlib import Path

from battle_system.app.schema_io import load_item_defs
from battle_system.core.models import UseSkillDef, VALID_ITEM_WEIGHTS


# -----------------------------------------------------------------------------
# Fixtures: 임시 YAML 파일 생성
# -----------------------------------------------------------------------------

@pytest.fixture()
def tmp_items_dir(tmp_path: Path) -> Path:
    d = tmp_path / "items"
    d.mkdir()
    return d


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# -----------------------------------------------------------------------------
# use_skill 없는 아이템 로드
# -----------------------------------------------------------------------------

def test_item_without_use_skill(tmp_items_dir: Path) -> None:
    """use_skill이 없는 아이템은 use_skill=None으로 로드되어야 한다."""
    _write_yaml(tmp_items_dir / "sword.yaml", """
item_id: IRON_SWORD
weight: 4
weapon_type: SWORD
attack_profile:
  crit_stat: STR
  range: MELEE
""")
    items = load_item_defs(tmp_items_dir)
    assert "IRON_SWORD" in items
    assert items["IRON_SWORD"].use_skill is None
    assert items["IRON_SWORD"].weight == 4


# -----------------------------------------------------------------------------
# use_skill 있는 아이템 로드
# -----------------------------------------------------------------------------

def test_item_with_use_skill_hp_delta(tmp_items_dir: Path) -> None:
    """use_skill이 포함된 아이템(힐링 물약)이 UseSkillDef로 정확히 파싱되어야 한다."""
    _write_yaml(tmp_items_dir / "potion.yaml", """
item_id: HEALING_POTION
weight: 1
use_skill:
  skill_id: USE_HEALING_POTION
  name: Healing Potion
  action_type: SUB
  target_filter: SELF
  steps:
    - kind: APPLY_HP_DELTA
      hp_delta: 10
      range: ANY
      area: SINGLE
""")
    items = load_item_defs(tmp_items_dir)
    item = items["HEALING_POTION"]
    assert item.use_skill is not None
    assert isinstance(item.use_skill, UseSkillDef)
    assert item.use_skill.skill_id == "USE_HEALING_POTION"
    assert item.use_skill.name == "Healing Potion"
    assert item.use_skill.action_type == "SUB"
    assert item.use_skill.target_filter == "SELF"
    assert len(item.use_skill.steps) == 1
    assert item.use_skill.steps[0].kind == "APPLY_HP_DELTA"
    assert item.use_skill.steps[0].hp_delta == 10


def test_item_with_use_skill_apply_effect(tmp_items_dir: Path) -> None:
    """상태이상 부여 아이템(독 물약)이 UseSkillDef로 정확히 파싱되어야 한다."""
    _write_yaml(tmp_items_dir / "poison_vial.yaml", """
item_id: POISON_VIAL
weight: 1
use_skill:
  skill_id: USE_POISON_VIAL
  name: Poison Vial
  action_type: SUB
  target_filter: ENEMY
  steps:
    - kind: APPLY_EFFECT
      effect_id: POISONED
      effect_duration: 3
      status_inflict: 5
      range: ANY
      area: SINGLE
""")
    items = load_item_defs(tmp_items_dir)
    item = items["POISON_VIAL"]
    assert item.use_skill is not None
    assert item.use_skill.target_filter == "ENEMY"
    assert item.use_skill.steps[0].effect_id == "POISONED"
    assert item.use_skill.steps[0].effect_duration == 3


# -----------------------------------------------------------------------------
# use_skill 기본값 확인
# -----------------------------------------------------------------------------

def test_use_skill_default_values(tmp_items_dir: Path) -> None:
    """use_skill 블록에서 생략된 필드는 기본값으로 채워져야 한다."""
    _write_yaml(tmp_items_dir / "herb.yaml", """
item_id: HERB
weight: 0
use_skill:
  steps:
    - kind: APPLY_HP_DELTA
      hp_delta: 3
""")
    items = load_item_defs(tmp_items_dir)
    us = items["HERB"].use_skill
    assert us is not None
    assert us.skill_id == "USE_ITEM"       # 기본값
    assert us.name == "Use Item"           # 기본값
    assert us.action_type == "SUB"         # 기본값
    assert us.cooldown_turns == 0          # 기본값
    assert us.crit_stat == "STR"           # 기본값
    assert us.target_filter == "ANY"       # 기본값


# -----------------------------------------------------------------------------
# weight 검증
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("valid_w", sorted(VALID_ITEM_WEIGHTS))
def test_valid_weights_accepted(tmp_items_dir: Path, valid_w: int) -> None:
    """허용된 weight 값(0,1,2,4,8,16)은 예외 없이 로드되어야 한다."""
    _write_yaml(tmp_items_dir / "item.yaml", f"""
item_id: TEST_ITEM
weight: {valid_w}
""")
    items = load_item_defs(tmp_items_dir)
    assert items["TEST_ITEM"].weight == valid_w


@pytest.mark.parametrize("invalid_w", [3, 5, 6, 7, 9, 10, 15, 32, -1])
def test_invalid_weight_raises(tmp_items_dir: Path, invalid_w: int) -> None:
    """허용되지 않은 weight 값은 ValueError를 발생시켜야 한다."""
    _write_yaml(tmp_items_dir / "bad.yaml", f"""
item_id: BAD_ITEM
weight: {invalid_w}
""")
    with pytest.raises(ValueError, match="invalid weight"):
        load_item_defs(tmp_items_dir)


# -----------------------------------------------------------------------------
# 혼합 로드: use_skill 있는 아이템 + 없는 아이템 동시
# -----------------------------------------------------------------------------

def test_mixed_items_with_and_without_use_skill(tmp_items_dir: Path) -> None:
    """use_skill 있는 아이템과 없는 아이템이 혼합되어도 모두 정상 로드되어야 한다."""
    _write_yaml(tmp_items_dir / "a_sword.yaml", """
item_id: IRON_SWORD
weight: 4
weapon_type: SWORD
attack_profile:
  crit_stat: STR
  range: MELEE
""")
    _write_yaml(tmp_items_dir / "b_potion.yaml", """
item_id: HEAL_POT
weight: 1
use_skill:
  skill_id: USE_HEAL
  name: Heal
  target_filter: SELF
  steps:
    - kind: APPLY_HP_DELTA
      hp_delta: 5
""")
    items = load_item_defs(tmp_items_dir)
    assert len(items) == 2
    assert items["IRON_SWORD"].use_skill is None
    assert items["HEAL_POT"].use_skill is not None
    assert items["HEAL_POT"].use_skill.skill_id == "USE_HEAL"
