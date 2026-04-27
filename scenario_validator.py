"""Scenario validator — structural checks + BFS solvability."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def __str__(self) -> str:
        lines = [f"[ERROR] {e}" for e in self.errors]
        lines += [f"[WARN]  {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "OK"


def validate(scenario: dict) -> ValidationResult:
    result = ValidationResult()
    _check_structure(scenario, result)
    if result.ok:
        _check_solvability(scenario, result)
    return result


def _check_structure(s: dict, r: ValidationResult) -> None:
    items: dict = s.get("items", {})
    locks: dict = s.get("locks", {})
    traps: list = s.get("traps", [])
    code_limits: dict = s.get("code_limits", {})

    for key in ("title", "intro", "items", "locks"):
        if key not in s:
            r.errors.append(f"必須キー '{key}' が存在しない")

    if "exit_door" not in items:
        r.errors.append("items に 'exit_door' が存在しない")
    if "door_lock" not in locks:
        r.errors.append("locks に 'door_lock' が存在しない")

    # 最終鍵がロック報酬経由でのみ入手可能か確認
    # contains はexamineで取り出せるため施錠の意味がなく、reward 経由のみ許可する
    final_key = locks.get("door_lock", {}).get("key_required")
    if final_key and final_key in items:
        reward_items = {lock.get("reward") for lock in locks.values()}
        contained = {c for item in items.values() for c in item.get("contains", [])}
        if final_key not in reward_items:
            if final_key in contained:
                r.errors.append(
                    f"door_lock の key_required '{final_key}' が contains 経由で取得可能。"
                    "contains アイテムは examine で解放されるため施錠の意味がない。"
                    "必ず数字錠の reward にせよ"
                )
            else:
                r.errors.append(
                    f"door_lock の key_required '{final_key}' が床に直接置かれている。"
                    "必ず数字錠の reward 経由で入手できる構造にせよ"
                )

    all_item_ids = set(items.keys())
    all_lock_ids = set(locks.keys())
    trap_ids = {t.get("trap_id") for t in traps if t.get("trap_id")}

    # items.contains
    for iid, item in items.items():
        for cid in item.get("contains", []):
            if cid not in all_item_ids:
                r.errors.append(f"items[{iid}].contains: '{cid}' は items に存在しない")

    # items.lock_id
    for iid, item in items.items():
        lid = item.get("lock_id")
        if lid and lid not in all_lock_ids:
            r.errors.append(f"items[{iid}].lock_id: '{lid}' は locks に存在しない")

    # locks.reward / key_required
    for lid, lock in locks.items():
        reward = lock.get("reward")
        if reward and reward not in all_item_ids:
            r.errors.append(f"locks[{lid}].reward: '{reward}' は items に存在しない")
        key = lock.get("key_required")
        if key and key not in all_item_ids:
            r.errors.append(f"locks[{lid}].key_required: '{key}' は items に存在しない")

    # traps.trigger.args[0]
    for trap in traps:
        trigger = trap.get("trigger", {})
        action = trigger.get("action", "")
        args = trigger.get("args", [])
        if not action:
            continue
        if action in ("examine", "pick_up", "use_item") and args:
            if args[0] not in all_item_ids:
                r.warnings.append(
                    f"trap '{trap.get('trap_id')}' trigger.args[0]: '{args[0]}' は items に存在しない"
                )
        if action == "enter_code" and args:
            if args[0] not in all_lock_ids:
                r.warnings.append(
                    f"trap '{trap.get('trap_id')}' trigger.args[0]: '{args[0]}' は locks に存在しない"
                )

    # code_limits
    for lid, limit in code_limits.items():
        if lid not in all_lock_ids:
            r.errors.append(f"code_limits: '{lid}' は locks に存在しない")
        exhaust = limit.get("exhaust_trap")
        if exhaust and exhaust not in trap_ids:
            r.errors.append(
                f"code_limits[{lid}].exhaust_trap: '{exhaust}' は traps に存在しない"
            )


def _check_solvability(s: dict, r: ValidationResult) -> None:
    """BFS over (visible, inventory, unlocked) states ignoring death traps."""
    items: dict = s.get("items", {})
    locks: dict = s.get("locks", {})

    # Death trap triggers to skip in BFS
    death_triggers: set[tuple[str, str]] = set()
    for trap in s.get("traps", []):
        if trap.get("severity", "death") == "death":
            trigger = trap.get("trigger", {})
            action = trigger.get("action", "")
            args = trigger.get("args", [])
            if action and args:
                death_triggers.add((action, args[0]))

    contained = {c for item in items.values() for c in item.get("contains", [])}
    init_visible = frozenset(iid for iid in items if iid not in contained)

    State = tuple[frozenset, frozenset, frozenset]
    start: State = (init_visible, frozenset(), frozenset())
    visited: set[State] = {start}
    queue: deque[State] = deque([start])

    def next_states(state: State) -> list[State]:
        visible, inv, unlocked = state
        results = []

        # examine — reveal contained items
        for iid in visible | inv:
            if ("examine", iid) in death_triggers:
                continue
            new_vis = set(visible)
            added = False
            for cid in items.get(iid, {}).get("contains", []):
                if cid not in visible and cid not in inv:
                    new_vis.add(cid)
                    added = True
            if added:
                results.append((frozenset(new_vis), inv, unlocked))

        # pick_up
        for iid in visible:
            if ("pick_up", iid) in death_triggers:
                continue
            if items.get(iid, {}).get("takeable"):
                results.append((visible - {iid}, inv | {iid}, unlocked))

        # use_item (key on locked target)
        for iid in inv:
            for tid in visible | inv:
                lock_id = items.get(tid, {}).get("lock_id")
                if not lock_id or lock_id in unlocked:
                    continue
                lock = locks.get(lock_id, {})
                if lock.get("key_required") == iid:
                    new_vis = set(visible)
                    reward = lock.get("reward")
                    if reward:
                        new_vis.add(reward)
                    results.append((frozenset(new_vis), inv, unlocked | {lock_id}))

        # enter_code — assume correct code is always available
        for tid in visible | inv:
            lock_id = items.get(tid, {}).get("lock_id")
            if not lock_id or lock_id in unlocked:
                continue
            lock = locks.get(lock_id, {})
            if "answer" in lock:
                new_vis = set(visible)
                reward = lock.get("reward")
                if reward:
                    new_vis.add(reward)
                results.append((frozenset(new_vis), inv, unlocked | {lock_id}))

        return results

    MIN_STEPS = 4  # 最低限必要なアクション数（これ未満は簡単すぎ）

    found = False
    min_depth = 0
    queue_with_depth: deque[tuple] = deque([(start, 0)])
    visited_depth: dict = {start: 0}

    while queue_with_depth:
        state, depth = queue_with_depth.popleft()
        if "door_lock" in state[2]:
            found = True
            min_depth = depth
            break
        for nxt in next_states(state):
            if nxt not in visited_depth:
                visited_depth[nxt] = depth + 1
                visited.add(nxt)
                queue_with_depth.append((nxt, depth + 1))

    if not found:
        r.errors.append(
            "解法なし: door_lock を開く手順が存在しない（死亡トラップを除いたBFS探索で到達不可）"
        )
    elif min_depth < MIN_STEPS:
        r.errors.append(
            f"シナリオが簡単すぎる: 最短クリアが {min_depth} ステップ（最低 {MIN_STEPS} 必要）。"
            "パズルの手順を増やせ"
        )
    else:
        r.warnings.append(f"解法あり（最短 {min_depth} ステップ / BFS状態数: {len(visited)}）")


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python scenario_validator.py <scenario.json>")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    result = validate(data)
    print(result)
    sys.exit(0 if result.ok else 1)
