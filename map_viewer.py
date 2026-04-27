"""
Companion map viewer — run in a separate terminal while runner.py is active.

Usage:
    python map_viewer.py

Reads /tmp/escape_loop_state.json written by runner.py and renders the
top-down map in real-time using Rich Live.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

STATE_FILE = Path(os.environ.get("ESCAPE_STATE_FILE", "/tmp/escape_loop_state.json"))
POLL_INTERVAL = 0.5
COLS = 5
COL_W = 22


# ── map rendering ──────────────────────────────────────────────


def _dw(s: str) -> int:
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def _fit(s: str, max_dw: int) -> str:
    out, w = "", 0
    for c in s:
        cw = 2 if ord(c) > 0x2E7F else 1
        if w + cw > max_dw:
            return out + "…"
        out += c
        w += cw
    return out


def _cell(iid: str, items: dict, visible: list, inventory: list, unlocked: list, current_pos: str = "") -> tuple[str, str]:
    item = items.get(iid, {})
    name = item.get("name", iid)
    lock_id = item.get("lock_id")
    locked = lock_id and lock_id not in unlocked
    is_here = iid == current_pos

    max_w = COL_W - 3
    max_wl = COL_W - 4
    prefix = "★" if is_here else " "

    if iid in inventory:
        return ("(持参済)", "dim")
    if iid not in visible:
        return ("", "")
    if locked:
        return (f"{prefix}[{_fit(name, max_wl)}*]", "bold yellow" if is_here else "yellow")
    return (f"{prefix}[{_fit(name, max_w)}]", "bold bright_cyan" if is_here else "bright_white")


def build_map_text(state: dict) -> Text:
    items: dict = state.get("scenario", {}).get("items", {})
    all_ids = list(items.keys())
    visible: list = state.get("visible", [])
    inventory: list = state.get("inventory", [])
    unlocked: list = state.get("unlocked", [])

    current_pos: str = state.get("current_pos", "")

    has_pos = any("map_pos" in items[iid] for iid in all_ids)
    positions: dict[str, tuple[int, int]] = {}
    if has_pos:
        for iid in all_ids:
            pos = items[iid].get("map_pos")
            if pos:
                positions[iid] = (int(pos[0]), int(pos[1]))
    else:
        for idx, iid in enumerate(all_ids):
            positions[iid] = (idx % COLS, idx // COLS)

    if not positions:
        return Text("(no map data)")

    max_x = max(p[0] for p in positions.values()) + 1
    max_y = max(p[1] for p in positions.values()) + 1

    grid: list[list[tuple[str, str]]] = [
        [("", "") for _ in range(max_x)] for _ in range(max_y)
    ]
    for iid, (x, y) in positions.items():
        grid[y][x] = _cell(iid, items, visible, inventory, unlocked, current_pos)

    result = Text()
    for r, row in enumerate(grid):
        if r > 0:
            result.append("\n")
            for c in range(max_x):
                if c > 0:
                    result.append("  ")
                result.append("─" * COL_W, style="dim")
            result.append("\n")
        else:
            result.append("\n")

        for c, (text, style) in enumerate(row):
            if c > 0:
                result.append("  ")
            result.append(text, style=style)
            result.append(" " * max(0, COL_W - _dw(text)))

    result.append("\n")
    return result


def build_display(state: dict) -> Text:
    title = state.get("title", "—")
    run_no = state.get("run_no", "?")
    max_runs = state.get("max_runs", "?")
    step = state.get("step", "?")
    max_steps = state.get("max_steps", "?")
    last_action = state.get("last_action", "")
    last_narration = state.get("last_narration", "")
    inventory = state.get("inventory", [])
    items = state.get("scenario", {}).get("items", {})
    inv_names = [items.get(i, {}).get("name", i) for i in inventory]
    won = state.get("won", False)

    out = Text()
    out.append(f" {title}", style="bold magenta")
    out.append(f"  Run {run_no}/{max_runs}", style="yellow")
    out.append(f"  Step {step}/{max_steps}\n", style="cyan")
    out.append("─" * 40 + "\n", style="dim")

    out.append(build_map_text(state))

    out.append("─" * 40 + "\n", style="dim")
    out.append(f" 手持ち: {'、'.join(inv_names) or 'なし'}\n", style="dim")

    if last_action:
        out.append(f" ▶ {last_action}\n", style="bold cyan")
    if last_narration:
        out.append(f" 「{last_narration}」\n", style="italic white")

    if won:
        out.append("\n 🎉 脱出成功！\n", style="bold yellow")

    return out


# ── main ───────────────────────────────────────────────────────


def main() -> None:
    console = Console()
    console.print(f"[dim]状態ファイル監視中: {STATE_FILE}[/dim]")
    console.print("[dim]runner.py を起動すると自動的にマップが表示されます[/dim]\n")

    last_mtime: float = 0.0

    with Live("", console=console, refresh_per_second=4, screen=False) as live:
        while True:
            try:
                mtime = STATE_FILE.stat().st_mtime if STATE_FILE.exists() else 0.0
                if mtime != last_mtime:
                    last_mtime = mtime
                    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    live.update(build_display(state))
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
