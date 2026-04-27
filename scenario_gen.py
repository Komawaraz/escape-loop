"""LLM-powered scenario generator — produces a valid souls-like scenario dict."""
from __future__ import annotations

import json
import random
import re
import sys

from llm_client import chat

_GEN_SYSTEM = """あなたはゲームデザイナーだ。脱出ゲームシナリオをJSONのみで出力する。
余計な説明・前置き・マークダウンは禁止。JSONだけを出力せよ。"""

_GEN_PROMPT = """\
以下の仕様でRuina（AI）が自律プレイする脱出ゲームのシナリオJSONを生成せよ。

テーマ: {theme}（難度: {difficulty}）

【必須要件】
- items: 6〜9個（必ず "exit_door" を含め lock_id: "door_lock" を設定する）
- locks: 2〜3個（必ず "door_lock" を含める。door_lock は key_required 方式のみ）
- 数字錠は最低1個含める（3〜4桁）
- traps: 2〜3個（severity は "death" か "warning"、death には memory_hint を必ず含める）
- code_limits: 数字錠すべてに max_attempts: 3 と exhaust_trap を設定する
- max_runs: 5

【JSONスキーマ】
{{
  "schema_version": "2.0",
  "title": "...",
  "intro": "50字以内の場面説明",
  "max_runs": 5,
  "items": {{
    "アイテムID": {{
      "name": "...",
      "examine": "調べたときの説明",
      "contains": ["含まれるアイテムID"],
      "takeable": true,
      "lock_id": "..."
    }}
  }},
  "locks": {{
    "錠前ID": {{
      "digits": 3,
      "answer": "数字文字列",
      "reward": "報酬アイテムID",
      "hint": "ヒント文"
    }},
    "door_lock": {{
      "key_required": "最終鍵アイテムID"
    }}
  }},
  "traps": [
    {{
      "trap_id": "...",
      "trigger": {{"action": "examine", "args": ["アイテムID"]}},
      "severity": "death",
      "death_message": "死亡描写（1〜2文）",
      "memory_hint": "次Run向けの警告（答えは含めない）"
    }}
  ],
  "code_limits": {{
    "数字錠ID": {{"max_attempts": 3, "exhaust_trap": "罠ID"}}
  }}
}}

不要フィールドは省略してよい。JSONのみ出力せよ。"""

_THEMES = [
    ("廃病院の霊安室", "hard"),
    ("魔女の実験室", "medium"),
    ("海賊船の宝物庫", "medium"),
    ("古代遺跡の石室", "hard"),
    ("廃工場の管理室", "medium"),
    ("貴族の秘密書斎", "easy"),
    ("宇宙ステーションの制御室", "hard"),
    ("童話の魔女の小屋", "easy"),
    ("地下迷宮の最深部", "hard"),
    ("呪われた修道院", "hard"),
    ("沈没船の船長室", "medium"),
    ("錬金術師の工房", "medium"),
]


def generate(theme: str | None = None, difficulty: str | None = None, max_retries: int = 3) -> dict:
    from scenario_validator import validate

    if not theme:
        theme, difficulty = random.choice(_THEMES)
    elif not difficulty:
        difficulty = "medium"

    prompt = _GEN_PROMPT.format(theme=theme, difficulty=difficulty)
    last_errors: str = ""

    for attempt in range(1, max_retries + 1):
        retry_note = (
            f"\n\n【前回の生成エラー（修正せよ）】\n{last_errors}" if last_errors else ""
        )
        messages = [
            {"role": "system", "content": _GEN_SYSTEM},
            {"role": "user", "content": prompt + retry_note},
        ]
        raw = chat(messages, temperature=0.85, max_tokens=2500, json_mode=True)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        text = m.group() if m else raw

        try:
            scenario = json.loads(text)
        except json.JSONDecodeError as e:
            last_errors = f"JSONパースエラー: {e}"
            continue

        result = validate(scenario)
        if result.ok:
            return scenario

        last_errors = str(result)
        if attempt < max_retries:
            continue

    raise RuntimeError(f"シナリオ生成が{max_retries}回失敗:\n{last_errors}")
