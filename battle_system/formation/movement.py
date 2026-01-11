from __future__ import annotations

from battle_system.core.types import CombatantID, GroupID
from battle_system.core.models import BattleState


def _next_group_id(bs: BattleState) -> GroupID:
    """
    현재 존재하는 그룹 id들 중 최대값 + 1을 새 그룹 id로 사용.
    그룹이 비어있는 경우(이론상) 0부터 시작.
    """
    if not bs.groups:
        return GroupID(0)
    max_gid = max(int(gid) for gid in bs.groups.keys())
    return GroupID(max_gid + 1)


def _remove_member(bs: BattleState, gid: GroupID, cid: CombatantID) -> None:
    """
    그룹 멤버십에서 cid를 제거.
    제거 후 그룹이 비면, groups에서 해당 gid 엔트리를 삭제한다.
    """
    members = bs.groups.get(gid)
    if members is None:
        raise ValueError(f"Group {gid} does not exist.")
    try:
        members.remove(cid)
    except ValueError as e:
        raise ValueError(f"{cid} is not in group {gid}.") from e

    if len(members) == 0:
        del bs.groups[gid]


def _add_member(bs: BattleState, gid: GroupID, cid: CombatantID) -> None:
    """
    그룹 멤버십에 cid를 추가.
    groups에 gid가 없으면 새로 만든다.
    중복 추가는 허용하지 않는다.
    """
    if gid not in bs.groups:
        bs.groups[gid] = []

    if cid in bs.groups[gid]:
        raise ValueError(f"{cid} already in group {gid}.")
    bs.groups[gid].append(cid)


def engage(bs: BattleState, actor: CombatantID, target: CombatantID) -> None:
    """
    ENGAGE: actor가 target이 속한 그룹으로 합류한다.
    - actor와 target이 동일하면 의미 없는 요청이므로 예외.
    - actor의 기존 그룹에서 제거 후, target 그룹에 추가.
    - actor의 group_id를 target group으로 갱신.
    """
    if actor == target:
        raise ValueError("ENGAGE: actor and target must be different.")

    if actor not in bs.combatants or target not in bs.combatants:
        raise ValueError("ENGAGE: actor/target must exist in battle.")

    actor_gid = bs.combatants[actor].group_id
    target_gid = bs.combatants[target].group_id

    # 이미 같은 그룹이면 변화 없음. (예외 처리하는 경우는 우선 주석으로 처리)
    if actor_gid == target_gid:
        # raise ValueError("ENGAGE: already in the same group.")
        return

    _remove_member(bs, actor_gid, actor)
    _add_member(bs, target_gid, actor)
    bs.combatants[actor].group_id = target_gid


def disengage(bs: BattleState, actor: CombatantID) -> GroupID:
    """
    DISENGAGE: actor가 백스텝으로 독립 그룹을 생성한다.
    - actor의 기존 그룹에서 제거 후, 새 그룹을 만들어 actor만 넣는다.
    - actor의 group_id를 새 그룹으로 갱신.
    - 생성된 GroupID를 반환한다.
    """
    if actor not in bs.combatants:
        raise ValueError("DISENGAGE: actor must exist in battle.")

    old_gid = bs.combatants[actor].group_id
    new_gid = _next_group_id(bs)

    _remove_member(bs, old_gid, actor)
    _add_member(bs, new_gid, actor)
    bs.combatants[actor].group_id = new_gid
    return new_gid
