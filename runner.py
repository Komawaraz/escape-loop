"""AI自律脱出ゲームランナー — Souls-like マルチラン版"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_STATE_FILE = Path(os.environ.get("ESCAPE_STATE_FILE", "/tmp/escape_loop_state.json"))

from rich.console import Console
from rich.live import Live
from rich.rule import Rule
from rich.text import Text

from llm_client import chat

from engine import ActionResult, EscapeEngine
from memory import MemoryStore, RunRecord
from scenario_gen import generate as gen_scenario

console = Console()


class GameLogger:
    def __init__(self, title: str, char_name: str) -> None:
        logs_dir = Path(__file__).resolve().parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
        self._path = logs_dir / f"{ts}_{safe_title}.log"
        self._f = self._path.open("w", encoding="utf-8")
        self._write(f"=== {title} ===")
        self._write(f"キャラクター: {char_name}")
        self._write(f"開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._write("")

    def room_start(self, room_no: int, total_rooms: int, title: str) -> None:
        self._write(f"{'═' * 40}")
        self._write(f"部屋 {room_no}/{total_rooms}: {title}")
        self._write(f"{'═' * 40}")

    def run_start(self, run_no: int, max_runs: int) -> None:
        self._write(f"{'─' * 40}")
        self._write(f"Run {run_no}/{max_runs} 開始")
        self._write(f"{'─' * 40}")

    def step(self, step: int, action_str: str, narration: str, result: str) -> None:
        self._write(f"\n[Step {step}] {action_str}")
        if narration:
            self._write(f"  「{narration}」")
        self._write(f"  → {result}")

    def run_end(self, outcome: str, steps: int, diary: str) -> None:
        label = {"cleared": "脱出成功", "died": "死亡", "timeout": "タイムアウト"}.get(outcome, outcome)
        self._write(f"\n結果: {label}  ({steps}ターン)")
        self._write(f"振り返り: {diary}")

    def game_end(self, outcome: str) -> None:
        self._write(f"\n{'=' * 40}")
        self._write(f"ゲーム終了: {'GAME CLEARED' if outcome == 'cleared' else 'GAME OVER'}")
        self._write(f"終了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.close()
        console.print(f"[dim]ログ保存: {self._path}[/dim]")

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def _write(self, line: str) -> None:
        self._f.write(line + "\n")
        self._f.flush()


def _stop_map_viewer() -> None:
    subprocess.run(["pkill", "-f", "map_viewer.py"], capture_output=True)


def _launch_map_viewer() -> None:
    try:
        already = subprocess.run(["pgrep", "-f", "map_viewer.py"], capture_output=True)
        if already.returncode == 0:
            return
    except FileNotFoundError:
        pass

    viewer = Path(__file__).resolve().parent / "map_viewer.py"
    if not viewer.exists():
        return

    python = sys.executable
    cmd = f"{python} {viewer}"

    # Case 1: tmux内 → 右ペインに分割
    if os.environ.get("TMUX"):
        if os.system(f"tmux split-window -h '{cmd}'") == 0:
            atexit.register(_stop_map_viewer)
            return

    # Case 2: tmux外でもtmuxが入っている → 既存セッションの新規ウィンドウで開く
    try:
        sessions = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True,
        )
        if sessions.returncode == 0 and sessions.stdout.strip():
            session = sessions.stdout.strip().splitlines()[0]
            if os.system(f"tmux new-window -t '{session}' '{cmd}'") == 0:
                console.print(f"[dim]map_viewer: tmux セッション '{session}' の新規ウィンドウで起動[/dim]")
                atexit.register(_stop_map_viewer)
                return
    except FileNotFoundError:
        pass

    # Case 3: tmuxなし → バックグラウンド起動
    subprocess.Popen(
        [python, str(viewer)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    console.print(f"[dim]map_viewer: バックグラウンドで起動しました[/dim]")
    console.print(f"[dim]  → 別端末で確認: python {viewer}[/dim]")
    atexit.register(_stop_map_viewer)

def _load_character(path: Path | None) -> dict:
    if path is None:
        path = Path(__file__).parent / "characters" / "default.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _make_system(char: dict) -> str:
    parts = [
        char.get("persona", f"あなたは{char.get('name', 'runner')}。脱出ゲームを一人でプレイしている。"),
        char.get("speech_style", ""),
        char.get("thinking_style", ""),
    ]
    return "\n".join(p for p in parts if p)


def _make_diary_system(char: dict) -> str:
    name = char.get("name", "runner")
    speech = char.get("speech_style", "")
    return (
        f"あなたは{name}。今回の挑戦を一文で振り返る。\n"
        + (f"{speech}\n" if speech else "")
        + "30文字以内で述べよ。"
    )


# ── 表示ヘルパー ───────────────────────────────────────────────

def _typewrite(text: str, style: str = "white", delay: float = 0.045) -> None:
    with Live("", console=console, refresh_per_second=30) as live:
        buf = ""
        for ch in text:
            buf += ch
            live.update(Text(buf, style=style))
            time.sleep(delay)


def _chat_with_spinner(messages: list[dict], label: str = "考え中……", **kwargs) -> str:
    result: list = [None]
    err: list = [None]

    def _call() -> None:
        try:
            result[0] = chat(messages, **kwargs)
        except Exception as e:
            err[0] = e

    t = threading.Thread(target=_call)
    t.start()
    with console.status(f"[dim magenta]{label}[/dim magenta]", spinner="dots2"):
        t.join()
    if err[0]:
        raise err[0]
    return result[0]


def _repair_json(text: str) -> str:
    # 末尾カンマを除去 ("key": "val",} や "val",])
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _parse_action(text: str) -> dict:
    for candidate in (text, re.search(r"\{.*\}", text, re.DOTALL) and re.search(r"\{.*\}", text, re.DOTALL).group()):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(_repair_json(candidate))
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("parse failed", text, 0)


def _status_bar(
    title: str, run_no: int, max_runs: int, step: int, max_steps: int, game: EscapeEngine
) -> None:
    inv = "、".join(game.name(i) for i in game.inventory) or "なし"
    console.print(Rule(style="magenta"))
    console.print(
        f"[bold magenta]{title}[/bold magenta]  "
        f"[yellow]Run {run_no}/{max_runs}[/yellow]  "
        f"[cyan]Step {step}/{max_steps}[/cyan]  "
        f"[dim]手持ち: {inv}[/dim]"
    )
    console.print(Rule(style="magenta"))


def _you_died(reason: str) -> None:
    console.print()
    time.sleep(0.5)
    console.print(Rule(style="red"))
    console.print()
    _typewrite("YOU  DIED", style="bold red", delay=0.12)
    console.print()
    time.sleep(0.8)
    _typewrite(reason, style="dim red", delay=0.04)
    console.print()
    console.print(Rule(style="red"))
    time.sleep(3.5)


def _write_state(
    scenario: dict,
    game: "EscapeEngine",
    run_no: int,
    max_runs: int,
    step: int,
    max_steps: int,
    last_action: str,
    last_narration: str,
    won: bool = False,
    current_pos: str = "",
    memo: list | None = None,
) -> None:
    state = {
        "title": scenario.get("title", ""),
        "run_no": run_no,
        "max_runs": max_runs,
        "step": step,
        "max_steps": max_steps,
        "visible": list(game.visible),
        "inventory": list(game.inventory),
        "unlocked": list(game.unlocked),
        "last_action": last_action,
        "last_narration": last_narration,
        "won": won,
        "current_pos": current_pos,
        "memo": memo or [],
        "scenario": scenario,
    }
    try:
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _lock_label(lid: str, locks: dict | None) -> str:
    if not locks:
        return lid
    lock = locks.get(lid, {})
    if "answer" in lock:
        digits = lock.get("digits", len(str(lock["answer"])))
        return f"{lid}（{digits}桁の数字錠→enter_code）"
    if "key_required" in lock:
        return f"{lid}（物理鍵錠→use_item）"
    return lid


def _build_user_msg(
    game: EscapeEngine,
    last_result: str,
    item_ids: list[str],
    lock_ids: list[str],
    memory: MemoryStore,
    char: dict | None = None,
    room_memo: list[str] | None = None,
    locks: dict | None = None,
) -> str:
    name = (char or {}).get("name", "runner")
    visible = "、".join(f"{game.name(i)}[{i}]" for i in game.visible) or "何もない"
    inv = "、".join(f"{game.name(i)}[{i}]" for i in game.inventory) or "何もない"
    mem_block = memory.to_prompt()

    tried_block = ""
    if room_memo:
        unique = list(dict.fromkeys(e.split(" ", 1)[1] for e in room_memo if " " in e))
        if unique:
            tried_block = "すでに試したこと（同じ行動を繰り返すな）: " + "、".join(unique) + "\n\n"

    return (
        (f"{mem_block}\n\n" if mem_block else "")
        + tried_block
        + f"前の結果: {last_result}\n\n"
        f"現在の状況 — 見えているもの: {visible} / 手持ち: {inv}\n\n"
        "次のアクションをJSON形式で返せ:\n"
        f'{{"narration": "{name}の独り言（1〜2文）", "action": "アクション名", "args": ["引数1", ...]}}\n\n'
        f"アクション: look_around / examine / pick_up / use_item / enter_code\n"
        f"アイテムID（[]内がargs に使うID）: {', '.join(item_ids)}\n"
        f"錠前ID: {', '.join(_lock_label(lid, locks) for lid in lock_ids)}\n\n"
        "【行動ルール】\n"
        "- look_around: 現在の状況と同じ情報しか返さない。既に全アイテムが見えているなら使うな。\n"
        "- use_item(A, B): AがBの錠前の key_required である場合のみ機能する。鍵と錠の対応がなければ何も起きない。闇雲な組み合わせ試行は禁止。\n"
        "- enter_code: 数字錠IDと推測したコードを指定する。examine で集めたヒントから数字を論理的に導いて入力せよ。"
    )


def _generate_diary(
    title: str, run_no: int, steps: int, outcome: str, sample_narration: str,
    char: dict | None = None,
) -> str:
    messages = [
        {"role": "system", "content": _make_diary_system(char or {})},
        {
            "role": "user",
            "content": (
                f"「{title}」Run{run_no}の記録。{steps}ターン、結果: {outcome}。"
                f"最後の独り言: 「{sample_narration}」\n30文字以内で一言。"
            ),
        },
    ]
    try:
        return chat(messages, temperature=0.7, max_tokens=60).strip()
    except Exception:
        return f"Run{run_no}: {outcome}"


_ACTION_JP = {
    "look_around": "見回す",
    "examine": "調べる",
    "pick_up": "拾う",
    "use_item": "使う",
    "enter_code": "コード入力",
}


def _memo_entry(run_no: int, step: int, action: str, args: list[str], game: "EscapeEngine") -> str:
    label = _ACTION_JP.get(action, action)
    if action == "look_around":
        return f"R{run_no}S{step} {label}"
    if action in ("examine", "pick_up") and args:
        return f"R{run_no}S{step} {label}({game.name(args[0])})"
    if action == "use_item" and len(args) >= 2:
        return f"R{run_no}S{step} {label}({game.name(args[0])}→{game.name(args[1])})"
    if action == "enter_code" and args:
        code = args[1] if len(args) > 1 else "?"
        return f"R{run_no}S{step} {label}({args[0]}, {code})"
    return f"R{run_no}S{step} {label}({', '.join(args)})"


# ── シングルラン ───────────────────────────────────────────────

def _run_single(
    scenario: dict,
    run_no: int,
    max_runs: int,
    max_steps: int,
    memory: MemoryStore,
    step_delay: float = 4.0,
    char: dict | None = None,
    logger: "GameLogger | None" = None,
    room_memo: list[str] | None = None,
) -> tuple[str, int, str]:
    """Returns (outcome, steps, last_narration)."""
    char = char or {}
    name = char.get("name", "runner")
    game = EscapeEngine(scenario)
    item_ids = list(scenario["items"].keys())
    lock_ids = list(scenario["locks"].keys())
    title = scenario["title"]

    console.print()
    console.print(Rule(style="yellow"))
    console.print(f"[bold yellow]  Run {run_no} / {max_runs}  —  {title}[/bold yellow]")
    if run_no > 1 and memory.danger_flags:
        console.print(f"[dim red]記憶: {memory.danger_flags[-1]}[/dim red]")
    console.print(Rule(style="yellow"))
    console.print()
    time.sleep(1.5)

    mem_intro = memory.to_prompt()
    assistant_opening = (
        f"{mem_intro}\nまた戻ってきた。今度こそ脱出する。"
        if mem_intro
        else "ここはどこだろう。脱出しなければならない。まず周囲を把握する。"
    )

    messages: list[dict] = [
        {"role": "system", "content": _make_system(char)},
        {"role": "user", "content": f"脱出ゲーム「{title}」Run{run_no}が始まった。{scenario['intro']}"},
        {"role": "assistant", "content": assistant_opening},
    ]

    last_result = f"Run{run_no} 開始"
    last_narration = ""
    current_pos = ""

    for step in range(1, max_steps + 1):
        user_msg = _build_user_msg(game, last_result, item_ids, lock_ids, memory, char, room_memo, locks=scenario.get("locks"))
        messages.append({"role": "user", "content": user_msg})

        console.print()
        _status_bar(title, run_no, max_runs, step, max_steps, game)

        try:
            raw = _chat_with_spinner(
                messages, label=f"{name}、考え中……",
                temperature=0.8, max_tokens=450, json_mode=True,
            )
            parsed = _parse_action(raw)
        except Exception as e:
            console.print(f"[red]応答エラー: {e}[/red]")
            fallback = '{"narration": "少し考え直す", "action": "look_around", "args": []}'
            messages.append({"role": "assistant", "content": fallback})
            last_result = game.execute("look_around", []).message
            continue

        narration = parsed.get("narration", "")
        action = parsed.get("action", "look_around")
        args = [str(a) for a in parsed.get("args", [])]
        messages.append({"role": "assistant", "content": raw})

        time.sleep(step_delay * 0.5)

        action_str = f"{action}({', '.join(args)})" if args else action
        console.print(f"\n[bold cyan]▶ {action_str}[/bold cyan]")
        time.sleep(0.8)

        console.print(f"[magenta]{name}:[/magenta]")
        _typewrite(f"「{narration}」", style="italic white", delay=0.055)
        last_narration = narration
        time.sleep(step_delay * 0.4)

        result: ActionResult = game.execute(action, args)
        _typewrite(result.message, style="yellow", delay=0.03)
        last_result = result.message
        current_pos = args[0] if args else current_pos
        if room_memo is not None:
            room_memo.append(_memo_entry(run_no, step, action, args, game))
        _write_state(scenario, game, run_no, max_runs, step, max_steps, action_str, narration, current_pos=current_pos, memo=room_memo)
        if logger:
            logger.step(step, action_str, narration, result.message)
        time.sleep(step_delay)

        if result.died:
            _you_died(result.death_reason)
            if result.death_memory_hint:
                memory.add_danger(result.death_memory_hint)
            memory.add_danger(f"【即死確認】{action_str} は実行禁止")
            return "died", step, last_narration

        if result.cleared:
            _write_state(scenario, game, run_no, max_runs, step, max_steps, action_str, narration, won=True, current_pos=current_pos, memo=room_memo)
            console.print()
            console.print(Rule(style="yellow"))
            console.print(
                f"[bold yellow]  脱出成功！  Run {run_no}/{max_runs}  "
                f"クリアターン: {step}[/bold yellow]"
            )
            console.print(Rule(style="yellow"))
            return "cleared", step, last_narration

    return "timeout", max_steps, last_narration


# ── 部屋ループ（単室・多室共用） ───────────────────────────────

def _run_room(
    scenario: dict,
    max_steps: int,
    step_delay: float,
    char: dict,
    memory: MemoryStore,
    logger: GameLogger,
    max_runs: int,
    room_no: int = 1,
    total_rooms: int = 1,
) -> tuple[str, int]:
    """Returns (outcome, runs_used). outcome: 'cleared' | 'over'."""
    name = char.get("name", "runner")
    title = scenario["title"]

    console.print()
    console.print(Rule(style="magenta"))
    if total_rooms > 1:
        console.print(
            f"[bold magenta]  部屋 {room_no}/{total_rooms}: {title}[/bold magenta]"
        )
    else:
        console.print(f"[bold magenta]  {title}[/bold magenta]")
    console.print(Rule(style="magenta"))
    console.print()
    _typewrite(scenario["intro"], style="dim white", delay=0.035)
    console.print()
    time.sleep(2.0)

    room_memo: list[str] = []
    for run_no in range(1, max_runs + 1):
        logger.run_start(run_no, max_runs)
        outcome, steps, last_narration = _run_single(
            scenario, run_no, max_runs, max_steps, memory, step_delay, char, logger,
            room_memo=room_memo,
        )

        with console.status("[dim]振り返り中……[/dim]", spinner="dots2"):
            diary = _generate_diary(title, run_no, steps, outcome, last_narration, char)
        memory.add_run(RunRecord(run_no=run_no, steps=steps, outcome=outcome, diary=diary))
        logger.run_end(outcome, steps, diary)

        if outcome == "cleared":
            console.print()
            console.print(Rule(style="bright_yellow"))
            console.print(
                f"[bold bright_yellow]  脱出成功！  Run {run_no}/{max_runs}  "
                f"クリアターン: {steps}[/bold bright_yellow]"
            )
            console.print(Rule(style="bright_yellow"))
            return "cleared", run_no

        if run_no < max_runs:
            console.print()
            console.print(f"[dim]次の挑戦へ……[/dim]")
            time.sleep(2.0)

    return "over", max_runs


# ── 単室エントリポイント ───────────────────────────────────────

def run(scenario: dict, max_steps: int = 30, step_delay: float = 4.0, char: dict | None = None) -> None:
    _launch_map_viewer()
    char = char or {}
    name = char.get("name", "runner")
    title = scenario["title"]
    max_runs = scenario.get("max_runs", 5)

    memory = MemoryStore()
    logger = GameLogger(title, name)

    outcome, runs_used = _run_room(
        scenario, max_steps, step_delay, char, memory, logger, max_runs
    )

    if outcome == "cleared":
        console.print()
        console.print(Rule(style="bright_yellow"))
        console.print(
            f"[bold bright_yellow]  GAME CLEARED  Total Runs: {runs_used}[/bold bright_yellow]"
        )
        console.print(Rule(style="bright_yellow"))
        logger.game_end("cleared")
    else:
        console.print()
        console.print(Rule(style="red"))
        console.print(f"[red]  {max_runs}回の挑戦、すべて失敗……  GAME OVER[/red]")
        console.print(Rule(style="red"))
        logger.game_end("over")


# ── マルチルームキャンペーン ───────────────────────────────────

def run_campaign(
    scenarios: list[dict],
    max_steps: int = 30,
    step_delay: float = 4.0,
    char: dict | None = None,
    hardcore: bool = False,
) -> None:
    _launch_map_viewer()
    char = char or {}
    name = char.get("name", "runner")
    total_rooms = len(scenarios)
    mode_label = "HARDCORE" if hardcore else "CAMPAIGN"
    campaign_title = f"{mode_label} — 全{total_rooms}部屋"
    logger = GameLogger(campaign_title, name)

    console.print()
    console.print(Rule(style="bright_cyan"))
    console.print(
        f"[bold bright_cyan]  {mode_label}: 全{total_rooms}部屋クリアを目指せ[/bold bright_cyan]"
    )
    console.print(Rule(style="bright_cyan"))
    time.sleep(2.0)

    # hardcore: 記憶を部屋間で引き継ぐ; normal: 部屋ごとにリセット
    shared_memory: MemoryStore | None = MemoryStore() if hardcore else None

    for room_no, scenario in enumerate(scenarios, 1):
        title = scenario["title"]
        max_runs = scenario.get("max_runs", 5)

        if room_no > 1:
            console.print()
            console.print(Rule(style="bright_cyan"))
            console.print(
                f"[bold bright_cyan]  次の部屋へ……  {room_no}/{total_rooms}[/bold bright_cyan]"
            )
            console.print(Rule(style="bright_cyan"))
            time.sleep(3.0)

        memory = shared_memory if hardcore else MemoryStore()
        logger.room_start(room_no, total_rooms, title)

        outcome, runs_used = _run_room(
            scenario, max_steps, step_delay, char, memory, logger, max_runs,
            room_no=room_no, total_rooms=total_rooms,
        )

        if outcome != "cleared":
            console.print()
            console.print(Rule(style="red"))
            console.print(
                f"[red]  部屋 {room_no}/{total_rooms} で力尽きた……  {mode_label} OVER[/red]"
            )
            console.print(Rule(style="red"))
            logger.game_end("over")
            return

    console.print()
    console.print(Rule(style="bright_yellow"))
    console.print(
        f"[bold bright_yellow]  全{total_rooms}部屋クリア！  {mode_label} CLEARED[/bold bright_yellow]"
    )
    console.print(Rule(style="bright_yellow"))
    logger.game_end("cleared")


# ── エントリポイント ───────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Souls-like Escape Game Runner")
    parser.add_argument("--scenario", type=Path, help="既存シナリオJSONのパス（省略: AI生成）")
    parser.add_argument("--theme", help="生成テーマ（省略: ランダム）")
    parser.add_argument("--character", type=Path, help="キャラクター設定JSONのパス（省略: characters/default.json）")
    parser.add_argument("--max-steps", type=int, default=30, help="1ラン最大ターン数")
    parser.add_argument("--step-delay", type=float, default=4.0, help="ステップ間の待機秒数（放送用: 6〜8）")
    parser.add_argument("--rooms", type=int, default=1, help="部屋数（2以上でマルチルームモード、AI生成のみ）")
    parser.add_argument("--hardcore", action="store_true", help="ハードコアモード: 記憶が部屋をまたいで引き継がれる")
    args = parser.parse_args()

    char_data = _load_character(args.character)
    char_name = char_data.get("name", "runner")

    # ── マルチルームモード ────────────────────────────────────────
    if args.rooms > 1:
        if args.scenario:
            console.print("[yellow]警告: --rooms > 1 では --scenario は無視されます（AI生成のみ）[/yellow]")

        console.print()
        console.print(Rule(style="cyan"))
        console.print(f"[bold cyan]  全{args.rooms}部屋のシナリオを生成中……[/bold cyan]")
        console.print(Rule(style="cyan"))

        generated_scenarios: list[dict] = []
        for i in range(args.rooms):
            with console.status(
                f"[dim cyan]{char_name}、部屋 {i + 1}/{args.rooms} を組み立て中……[/dim cyan]",
                spinner="dots2",
            ):
                s = gen_scenario(theme=args.theme)
            generated_scenarios.append(s)
            console.print(f"[cyan]  部屋 {i + 1}: {s.get('title')}[/cyan]")

        time.sleep(1.5)
        run_campaign(
            generated_scenarios,
            max_steps=args.max_steps,
            step_delay=args.step_delay,
            char=char_data,
            hardcore=args.hardcore,
        )

        console.print()
        try:
            answer = input("生成したシナリオを保存しますか？ [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer == "y":
            scenarios_dir = Path(__file__).resolve().parent / "scenarios"
            scenarios_dir.mkdir(exist_ok=True)
            for s in generated_scenarios:
                safe_title = re.sub(r'[\\/:*?"<>|]', "_", s.get("title", "scenario"))
                save_path = scenarios_dir / f"{safe_title}.json"
                save_path.write_text(
                    json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                console.print(f"[cyan]保存: {save_path}[/cyan]")

    # ── 単室モード ────────────────────────────────────────────────
    else:
        ai_generated = False
        if args.scenario:
            with open(args.scenario, encoding="utf-8") as f:
                scenario_data = json.load(f)
            console.print(f"[cyan]シナリオ読み込み: {scenario_data.get('title')}[/cyan]")
        else:
            console.print()
            console.print(Rule(style="cyan"))
            console.print("[bold cyan]  シナリオ生成中……[/bold cyan]")
            console.print(Rule(style="cyan"))
            with console.status(f"[dim cyan]{char_name}、舞台を組み立て中……[/dim cyan]", spinner="dots2"):
                scenario_data = gen_scenario(theme=args.theme)
            console.print(f"[cyan]生成完了: {scenario_data.get('title')}[/cyan]")
            ai_generated = True
            time.sleep(1.5)

        run(scenario_data, max_steps=args.max_steps, step_delay=args.step_delay, char=char_data)

        if ai_generated:
            console.print()
            try:
                answer = input("このシナリオを保存しますか？ [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer == "y":
                scenarios_dir = Path(__file__).resolve().parent / "scenarios"
                scenarios_dir.mkdir(exist_ok=True)
                safe_title = re.sub(r'[\\/:*?"<>|]', "_", scenario_data.get("title", "scenario"))
                save_path = scenarios_dir / f"{safe_title}.json"
                save_path.write_text(
                    json.dumps(scenario_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                console.print(f"[cyan]保存しました: {save_path}[/cyan]")
