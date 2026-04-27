"""LLM client — OpenAI-compatible, configured via environment variables.

Required env vars (set in .env):
  OPENAI_API_KEY   — API key (use "dummy" for local servers like vLLM/Ollama)
  OPENAI_BASE_URL  — Base URL (e.g. https://api.openai.com/v1 or http://localhost:8000/v1)
  MODEL_NAME       — Model ID (e.g. gpt-4o or Qwen/Qwen3.6-35B-A3B-FP8)

Optional:
  ENABLE_THINKING  — Set "true" to pass enable_thinking=true (vLLM Qwen3 only)
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_API_KEY = os.environ.get("OPENAI_API_KEY", "dummy")
_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
_MODEL = os.environ.get("MODEL_NAME", "gpt-4o-mini")

# ENABLE_THINKING が明示されている場合のみ extra_body で thinking 制御を送る
# "false" → enable_thinking: false（Qwen3のthinkingタグを無効化）
# "true"  → enable_thinking: true
# 未設定  → 送らない（OpenAI等の標準API向け）
_THINKING_ENV = os.environ.get("ENABLE_THINKING", "").lower()
_THINKING_EXTRA: dict | None = (
    {"chat_template_kwargs": {"enable_thinking": _THINKING_ENV == "true"}}
    if _THINKING_ENV in ("true", "false")
    else None
)

_client = OpenAI(api_key=_API_KEY, base_url=_BASE_URL)


def chat(
    messages: list[dict],
    temperature: float = 0.8,
    max_tokens: int = 512,
    json_mode: bool = False,
) -> str:
    kwargs: dict = dict(
        model=_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if _THINKING_EXTRA is not None:
        kwargs["extra_body"] = _THINKING_EXTRA

    resp = _client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""
