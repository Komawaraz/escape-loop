"""Ruina 自律脱出ゲームランナー — Souls-like マルチラン版"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.rule import Rule
from rich.text import Text

from llm_client import chat

from engine import ActionResult, EscapeEngine
from memory import MemoryStore, RunRecord
from scenario_gen import generate as gen_scenario

console = Console()

_SYSTEM = """\
あなたはRuina（ルイナ）。脱出ゲームを一人でプレイしている。
一人称は「わたし」。語尾は「だ・だろう・かもしれない・のだ」調（です/ます禁止）。絵文字なし。日本語のみ。
観察した手がかりから論理的に推理し、積極的に探索するのだ。
罠で死んでも諦めない。記憶を活かして次の挑戦に臨むのだ。"""

_DIARY_SYSTEM = """\
あなたはRuina（ルイナ）。今回の挑戦を一文で振り返る。
一人称は「わたし」。語尾は「だ・だろう・かもしれない・のだ」調（です/ます禁止）。絵文字なし。日本語のみ。
30文字以内で述べよ。"""


# ── 表示ヘルパー ───────────────────────────────────────────────

def _typewrite(text: str, style: str = "white", delay: float = 0.045) -> None:
    with Live("", console=console, refresh_per_second=30) as live:
        buf = ""
        for ch in text:
            buf += ch
            live.update(Text(buf, style=style))
            time.sleep(delay)


def _chat_with_spinner(messages: list[dict], label: str = "Ruina、考え中……", **kwargs) -> str:
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


def _parse_action(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise


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


def _build_user_msg(
    game: EscapeEngine,
    last_result: str,
    item_ids: list[str],
    lock_ids: list[str],
    memory: MemoryStore,
) -> str:
    visible = "、".join(game.name(i) for i in game.visible) or "何もない"
    inv = "、".join(game.name(i) for i in game.inventory) or "何もない"
    mem_block = memory.to_prompt()
    return (
        (f"{mem_block}\n\n" if mem_block else "")
        + f"前の結果: {last_result}\n\n"
        f"現在の状況 — 見えているもの: {visible} / 手持ち: {inv}\n\n"
        "次のアクションをJSON形式で返せ:\n"
        '{"narration": "Ruinaの独り言（1〜2文）", "action": "アクション名", "args": ["引数1", ...]}\n\n'
        f"アクション: look_around / examine / pick_up / use_item / enter_code\n"
        f"アイテムID: {', '.join(item_ids)}\n"
        f"錠前ID: {', '.join(lock_ids)}"
    )


def _generate_diary(
    title: str, run_no: int, steps: int, outcome: str, sample_narration: str
) -> str:
    messages = [
        {"role": "system", "content": _DIARY_SYSTEM},
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


# ── シングルラン ───────────────────────────────────────────────

def _run_single(
    scenario: dict,
    run_no: int,
    max_runs: int,
    max_steps: int,
    memory: MemoryStore,
    step_delay: float = 4.0,
) -> tuple[str, int, str]:
    """Returns (outcome, steps, last_narration)."""
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
        f"{mem_intro}\nわたしはまた戻ってきたのだ。今度こそ脱出する。"
        if mem_intro
        else "ここはどこだろう。わたしは脱出しなければならないのだ。まず周囲を把握する。"
    )

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"脱出ゲーム「{title}」Run{run_no}が始まった。{scenario['intro']}"},
        {"role": "assistant", "content": assistant_opening},
    ]

    last_result = f"Run{run_no} 開始"
    last_narration = ""

    for step in range(1, max_steps + 1):
        user_msg = _build_user_msg(game, last_result, item_ids, lock_ids, memory)
        messages.append({"role": "user", "content": user_msg})

        console.print()
        _status_bar(title, run_no, max_runs, step, max_steps, game)

        try:
            raw = _chat_with_spinner(messages, temperature=0.8, max_tokens=200, json_mode=True)
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

        console.print("[magenta]Ruina:[/magenta]")
        _typewrite(f"「{narration}」", style="italic white", delay=0.055)
        last_narration = narration
        time.sleep(step_delay * 0.4)

        result: ActionResult = game.execute(action, args)
        _typewrite(result.message, style="yellow", delay=0.03)
        last_result = result.message
        time.sleep(step_delay)

        if result.died:
            _you_died(result.death_reason)
            if result.death_memory_hint:
                memory.add_danger(result.death_memory_hint)
            # 具体的なアクションも記録して次Runで避けられるようにする
            memory.add_danger(f"【即死確認】{action_str} は実行禁止")
            return "died", step, last_narration

        if result.cleared:
            console.print()
            console.print(Rule(style="yellow"))
            console.print(
                f"[bold yellow]  脱出成功！  Run {run_no}/{max_runs}  "
                f"クリアターン: {step}[/bold yellow]"
            )
            console.print(Rule(style="yellow"))
            return "cleared", step, last_narration

    return "timeout", max_steps, last_narration


# ── メインループ ───────────────────────────────────────────────

def run(scenario: dict, max_steps: int = 30, step_delay: float = 4.0) -> None:
    title = scenario["title"]
    max_runs = scenario.get("max_runs", 5)

    console.print()
    console.print(Rule(style="magenta"))
    console.print(f"[bold magenta]  {title}[/bold magenta]")
    console.print(Rule(style="magenta"))
    console.print()
    _typewrite(scenario["intro"], style="dim white", delay=0.035)
    console.print()
    time.sleep(2.0)

    memory = MemoryStore()

    for run_no in range(1, max_runs + 1):
        outcome, steps, last_narration = _run_single(
            scenario, run_no, max_runs, max_steps, memory, step_delay
        )

        with console.status("[dim]振り返り中……[/dim]", spinner="dots2"):
            diary = _generate_diary(title, run_no, steps, outcome, last_narration)
        memory.add_run(RunRecord(run_no=run_no, steps=steps, outcome=outcome, diary=diary))

        if outcome == "cleared":
            console.print()
            console.print(Rule(style="bright_yellow"))
            console.print(
                f"[bold bright_yellow]  GAME CLEARED  Total Runs: {run_no}[/bold bright_yellow]"
            )
            console.print(Rule(style="bright_yellow"))
            return

        if run_no < max_runs:
            console.print()
            console.print(f"[dim]次の挑戦へ……[/dim]")
            time.sleep(2.0)

    console.print()
    console.print(Rule(style="red"))
    console.print(f"[red]  {max_runs}回の挑戦、すべて失敗……  GAME OVER[/red]")
    console.print(Rule(style="red"))


# ── エントリポイント ───────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ruina Souls-like Escape Game Runner")
    parser.add_argument("--scenario", type=Path, help="既存シナリオJSONのパス（省略: AI生成）")
    parser.add_argument("--theme", help="生成テーマ（省略: ランダム）")
    parser.add_argument("--max-steps", type=int, default=30, help="1ラン最大ターン数")
    parser.add_argument("--step-delay", type=float, default=4.0, help="ステップ間の待機秒数（放送用: 6〜8）")
    args = parser.parse_args()

    if args.scenario:
        with open(args.scenario, encoding="utf-8") as f:
            scenario_data = json.load(f)
        console.print(f"[cyan]シナリオ読み込み: {scenario_data.get('title')}[/cyan]")
    else:
        console.print()
        console.print(Rule(style="cyan"))
        console.print("[bold cyan]  シナリオ生成中……[/bold cyan]")
        console.print(Rule(style="cyan"))
        with console.status("[dim cyan]Ruina、舞台を組み立て中……[/dim cyan]", spinner="dots2"):
            scenario_data = gen_scenario(theme=args.theme)
        console.print(f"[cyan]生成完了: {scenario_data.get('title')}[/cyan]")
        time.sleep(1.5)

    run(scenario_data, max_steps=args.max_steps, step_delay=args.step_delay)
