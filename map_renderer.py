"""ASCII top-down map renderer — god's eye view of the escape room."""
from __future__ import annotations

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

COLS = 5       # default columns when no map_pos defined
COL_W = 22     # display width per column (accounts for CJK double-width)


def _dw(s: str) -> int:
    """Display width: CJK chars count as 2."""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def _fit(s: str, max_dw: int) -> str:
    """Truncate string to fit within max display width."""
    out, w = "", 0
    for c in s:
        cw = 2 if ord(c) > 0x2E7F else 1
        if w + cw > max_dw:
            return out + "…"
        out += c
        w += cw
    return out


def _cell(iid: str, game, current_pos: str = "") -> tuple[str, str]:
    """Return (display_text, rich_style) for one item cell."""
    item = game.scenario["items"].get(iid, {})
    name = item.get("name", iid)
    lock_id = item.get("lock_id")
    locked = lock_id and lock_id not in game.unlocked
    is_here = iid == current_pos

    max_w = COL_W - 3   # prefix(1) + "[" + name + "]"
    max_wl = COL_W - 4  # prefix(1) + "[" + name + "*]"
    prefix = "★" if is_here else " "

    if iid in game.inventory:
        return ("(持参済)", "dim")
    if iid not in game.visible:
        return ("", "")
    if locked:
        return (f"{prefix}[{_fit(name, max_wl)}*]", "bold yellow" if is_here else "yellow")
    return (f"{prefix}[{_fit(name, max_w)}]", "bold bright_cyan" if is_here else "bright_white")


def render(game, console: Console, current_pos: str = "") -> None:
    items = game.scenario["items"]
    all_ids = list(items.keys())
    if not all_ids:
        return

    # Use map_pos from JSON if present, otherwise auto-grid
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
        return

    max_x = max(p[0] for p in positions.values()) + 1
    max_y = max(p[1] for p in positions.values()) + 1

    # Build 2D grid of cells
    grid: list[list[tuple[str, str]]] = [
        [("", "") for _ in range(max_x)] for _ in range(max_y)
    ]
    for iid, (x, y) in positions.items():
        grid[y][x] = _cell(iid, game, current_pos)

    # Render
    console.print(Rule("神目線マップ", style="dim blue"))

    for r, row in enumerate(grid):
        if r > 0:
            # Row separator
            sep = Text()
            for c in range(max_x):
                if c > 0:
                    sep.append("  ", style="")
                sep.append("─" * COL_W, style="dim")
            console.print("  ", sep)

        line = Text()
        for c, (text, style) in enumerate(row):
            if c > 0:
                line.append("  ", style="")
            line.append(text, style=style)
            line.append(" " * max(0, COL_W - _dw(text)))
        console.print("  ", line)

    console.print(Rule(style="dim blue"))
