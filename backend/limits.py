"""Run-time budgets shared by the agent loop and the run lifecycle.

A planning run is bounded on three independent axes so a misbehaving model
cannot silently grow cost or lock up a conversation:

* step budget      — already enforced in `backend/agent.py` (MAX_STEPS /
                     MAX_TOTAL_STEPS); a hard cap on model invocations.
* context budget   — a cap on the characters fed to the model each run, so the
                     prompt cannot grow without bound across retries/observations.
* wall-clock TTL   — a run that is abandoned (user walked away) or that runs far
                     longer than expected is refused on resume and stopped in place.

All limits are environment-configurable and read at call time, so tests can
inject them with `monkeypatch.setenv` regardless of import style.
"""
from __future__ import annotations

import json
import os


def context_budget_chars() -> int:
    """Maximum characters of conversation history sent to the model per run.

    ~28k chars covers a comfortable multi-step tool loop for a CJK-heavy prompt
    while leaving headroom under typical 8k–32k token windows.
    """
    return int(os.getenv('PLAN_CONTEXT_CHARS_BUDGET', '28000'))


def run_ttl_seconds() -> int:
    """Maximum lifetime of a run in seconds before it is considered stale.

    A run resumed after this window (e.g. the user answered a clarifying
    question the next day) is refused on restart; a long-running activation is
    stopped in place once it exceeds the window.
    """
    return int(os.getenv('PLAN_RUN_TTL_SECONDS', '1800'))


def context_chars(messages: list[dict]) -> int:
    """Deterministic character count of the model payload (no model calls)."""
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def estimate_tokens(messages: list[dict]) -> int:
    """Conservative, model-agnostic token estimate for observability only.

    CJK-heavy prompts average ~1.4 chars/token and ASCII ~4; we approximate the
    whole payload at 1.5 chars/token so the guard never under-budgets. This is
    never used to gate behaviour — only the char budget is authoritative.
    """
    return int(context_chars(messages) / 1.5)
