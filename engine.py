"""Pure game logic — no I/O, no LLM calls."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ActionResult:
    message: str
    died: bool = False
    death_reason: str = ""
    death_memory_hint: str = ""
    cleared: bool = False


class EscapeEngine:
    def __init__(self, scenario: dict) -> None:
        self.scenario = scenario
        contained = {
            c
            for item in scenario["items"].values()
            for c in item.get("contains", [])
        }
        self.visible: list[str] = [
            iid for iid in scenario["items"] if iid not in contained
        ]
        self.inventory: list[str] = []
        self.unlocked: list[str] = []
        self.won: bool = False
        self._code_attempts: dict[str, int] = {}

    def name(self, iid: str) -> str:
        return self.scenario["items"].get(iid, {}).get("name", iid)

    def _check_traps(self, action: str, args: list[str]) -> ActionResult | None:
        for trap in self.scenario.get("traps", []):
            trigger = trap.get("trigger", {})
            if not trigger:
                continue
            if trigger.get("action") != action:
                continue
            trigger_args = trigger.get("args", [])
            if not all(
                i < len(args) and args[i] == expected
                for i, expected in enumerate(trigger_args)
            ):
                continue
            severity = trap.get("severity", "death")
            msg = trap.get("death_message", "罠が発動した……")
            hint = trap.get("memory_hint", "")
            if severity == "death":
                return ActionResult(
                    message=msg, died=True, death_reason=msg, death_memory_hint=hint
                )
            return ActionResult(message=f"[WARNING] {msg}")
        return None

    def execute(self, action: str, args: list[str]) -> ActionResult:
        if self.won:
            return ActionResult(message="すでに脱出成功！", cleared=True)

        trap_result = self._check_traps(action, args)
        if trap_result:
            return trap_result

        if action == "look_around":
            visible = "、".join(self.name(i) for i in self.visible) or "何もない"
            inv = "、".join(self.name(i) for i in self.inventory) or "何もない"
            return ActionResult(message=f"見えているもの: {visible} / 手持ち: {inv}")

        if action == "examine":
            iid = args[0] if args else ""
            item = self.scenario["items"].get(iid)
            if not item:
                return ActionResult(message=f"「{iid}」は見当たらない")
            if iid not in self.visible and iid not in self.inventory:
                return ActionResult(message=f"「{self.name(iid)}」は今は調べられない")
            result = item.get("examine", "特に何もない")
            revealed = []
            for cid in item.get("contains", []):
                if cid not in self.visible and cid not in self.inventory:
                    self.visible.append(cid)
                    revealed.append(self.name(cid))
            if revealed:
                result += f"\n（{'、'.join(revealed)}が見つかった！）"
            return ActionResult(message=result)

        if action == "pick_up":
            iid = args[0] if args else ""
            item = self.scenario["items"].get(iid)
            if not item:
                return ActionResult(message=f"「{iid}」は見当たらない")
            if not item.get("takeable"):
                return ActionResult(message=f"「{item['name']}」は持ち運べない")
            if iid in self.inventory:
                return ActionResult(message=f"「{item['name']}」はすでに手持ちにある")
            if iid not in self.visible:
                return ActionResult(message=f"「{item['name']}」はまず調べてみよう")
            self.visible.remove(iid)
            self.inventory.append(iid)
            return ActionResult(message=f"「{item['name']}」を拾った")

        if action == "use_item":
            iid = args[0] if len(args) > 0 else ""
            tid = args[1] if len(args) > 1 else ""
            if iid not in self.inventory:
                return ActionResult(message=f"「{self.name(iid)}」は手持ちにない")
            target = self.scenario["items"].get(tid)
            if not target:
                return ActionResult(message=f"「{tid}」は見当たらない")
            lock_id = target.get("lock_id")
            if lock_id:
                lock = self.scenario["locks"].get(lock_id, {})
                if lock.get("key_required") == iid:
                    self.unlocked.append(lock_id)
                    if lock_id == "door_lock":
                        self.won = True
                        return ActionResult(
                            message=f"「{target['name']}」が開いた！\n\n脱出成功！",
                            cleared=True,
                        )
                    reward = lock.get("reward")
                    msg = f"「{target['name']}」が開いた！"
                    if reward:
                        self.visible.append(reward)
                        msg += f" 中から「{self.name(reward)}」が現れた！"
                    return ActionResult(message=msg)
            return ActionResult(
                message=f"「{self.name(iid)}」を「{target['name']}」に使ったが、何も起きなかった"
            )

        if action == "enter_code":
            lock_id = args[0] if len(args) > 0 else ""
            code = args[1].strip() if len(args) > 1 else ""
            lock = self.scenario["locks"].get(lock_id)
            if not lock:
                return ActionResult(message=f"「{lock_id}」という錠前は見当たらない")
            if lock_id in self.unlocked:
                return ActionResult(message="すでに開いている")

            max_attempts = (
                self.scenario.get("code_limits", {})
                .get(lock_id, {})
                .get("max_attempts", 0)
            )
            if max_attempts > 0:
                self._code_attempts[lock_id] = self._code_attempts.get(lock_id, 0) + 1
                if self._code_attempts[lock_id] > max_attempts:
                    exhaust_id = (
                        self.scenario.get("code_limits", {})
                        .get(lock_id, {})
                        .get("exhaust_trap", "")
                    )
                    for trap in self.scenario.get("traps", []):
                        if trap.get("trap_id") == exhaust_id:
                            msg = trap.get("death_message", "試行回数を超えた……")
                            hint = trap.get("memory_hint", "")
                            return ActionResult(
                                message=msg, died=True, death_reason=msg, death_memory_hint=hint
                            )
                    return ActionResult(message="これ以上試せない……", died=True, death_reason="入力回数制限超過")

            if code == lock.get("answer"):
                self.unlocked.append(lock_id)
                reward = lock.get("reward")
                msg = "正解！錠前が開いた！"
                if reward:
                    self.visible.append(reward)
                    msg += f" 中から「{self.name(reward)}」が現れた！"
                return ActionResult(message=msg)

            hint = lock.get("hint", "")
            remaining = ""
            if max_attempts > 0:
                used = self._code_attempts.get(lock_id, 0)
                remaining = f" （残り{max_attempts - used}回）"
            return ActionResult(
                message=f"「{code}」— 違う。{remaining}" + (f" ヒント: {hint}" if hint else "")
            )

        return ActionResult(message=f"未知のアクション: {action}")
