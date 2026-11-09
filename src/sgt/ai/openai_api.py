"""Minimal OpenAI client for structured trade analysis: model check, strict-JSON chat, a daily token budget.

The key (secret ``openai_key``) is only ever sent in the Authorization header and never logged. Models are settings
(``SGT_OPENAI_TRADE_MODEL`` per-trade reviews, ``SGT_OPENAI_REPORT_MODEL`` nightly report) checked against /v1/models,
so a wrong name fails loudly with the list of available ids instead of silently doing nothing.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx

from sgt.core.secrets import get_secret
from sgt.ops.store import OpsStore

log = logging.getLogger(__name__)
API = "https://api.openai.com/v1"
TRADE_MODEL = "gpt-5.6-luna"            # [VERIFY] owner 2026-11-09; override with SGT_OPENAI_TRADE_MODEL
REPORT_MODEL = "gpt-5.6-sol"            # [VERIFY] owner 2026-11-09; override with SGT_OPENAI_REPORT_MODEL
TIMEOUT, MAX_BYTES = 120.0, 2 * 1024 * 1024
DAILY_TOKENS = 400_000                  # hard daily cap across all calls (override SGT_AI_DAILY_TOKENS)


class AIError(RuntimeError):
    pass


def trade_model() -> str:
    return os.environ.get("SGT_OPENAI_TRADE_MODEL", TRADE_MODEL)


def report_model() -> str:
    return os.environ.get("SGT_OPENAI_REPORT_MODEL", REPORT_MODEL)


def client() -> httpx.Client | None:
    key = get_secret("openai_key", required=False)
    if not key:
        return None
    return httpx.Client(headers={"Authorization": f"Bearer {key.strip()}"}, timeout=TIMEOUT, follow_redirects=False)


def models(c: httpx.Client) -> list[str]:
    r = c.get(f"{API}/models")
    if r.status_code != 200:
        err = (r.json().get("error") or {}).get("code") if "json" in r.headers.get("content-type", "") else None
        raise AIError(f"OpenAI /models -> {r.status_code} {err or ''}".strip())
    return sorted(m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict) and m.get("id"))


def require(c: httpx.Client, model: str) -> None:
    ids = models(c)
    if model not in ids:
        near = [i for i in ids if i.startswith(("gpt-5", "gpt-4.1", "o4", "o3"))] or ids
        raise AIError(f"model {model} not available; set the model env to one of: {', '.join(near[:30])}")


def _budget_key(now: datetime) -> str:
    return f"ai_tokens:{now.astimezone(timezone.utc).date().isoformat()}"


def spent(db: OpsStore, now: datetime) -> int:
    return int(db.flag(_budget_key(now)) or 0)


def chat_json(c: httpx.Client, db: OpsStore, model: str, system: str, user: str, schema: dict, max_out: int,
              now: datetime) -> dict:
    """One strict-JSON chat completion; refuses to run past the daily token budget; records usage."""
    cap = int(os.environ.get("SGT_AI_DAILY_TOKENS", DAILY_TOKENS))
    reserve = max_out                                           # reserve the worst-case answer up front (F-6)
    if not db.take(_budget_key(now), reserve, cap):            # atomic check-and-add: no two processes both pass
        raise AIError(f"daily AI token budget {cap} reached")
    body = {"model": model, "max_completion_tokens": max_out,
            "response_format": {"type": "json_schema", "json_schema": schema},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    effort = os.environ.get("SGT_OPENAI_REASONING")          # optional: "low" / "medium" for reasoning models
    if effort:
        body["reasoning_effort"] = effort
    try:
        return _call(c, db, body, reserve, now)
    except BilledError:
        raise
    except (AIError, httpx.HTTPError, ValueError):
        db.add(_budget_key(now), -reserve)                     # nothing was billed: give the reservation back
        raise


def _call(c: httpx.Client, db: OpsStore, body: dict, reserve: int, now: datetime) -> dict:
    with c.stream("POST", f"{API}/chat/completions", json=body) as r:
        if r.status_code != 200:
            r.read()
            err = (r.json().get("error") or {}) if "json" in r.headers.get("content-type", "") else {}
            raise AIError(f"OpenAI chat -> {r.status_code} {err.get('code') or err.get('type') or ''}".strip())
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > MAX_BYTES:
                raise AIError(f"OpenAI answer over {MAX_BYTES} bytes")
    j = json.loads(bytes(buf))
    used = int((j.get("usage") or {}).get("total_tokens") or 0)
    db.add(_budget_key(now), used - reserve)                    # settle the reservation to the real usage
    msg = ((j.get("choices") or [{}])[0].get("message") or {})
    try:                                                        # billed already: errors below keep the usage
        if msg.get("refusal"):
            raise ValueError("model refused")
        out = json.loads(msg.get("content") or "")
        if not isinstance(out, dict):
            raise ValueError("answer is not an object")
    except ValueError as exc:
        raise BilledError(str(exc) if str(exc) else "answer is not JSON") from exc
    return out


class BilledError(AIError):
    """The call was billed but the answer is unusable (the reservation is already settled; do not release it)."""


def s(x, n: int = 600) -> str:
    """Bounded string from untrusted model output."""
    return str(x)[:n] if x is not None else ""
