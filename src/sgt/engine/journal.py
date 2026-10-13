"""Persist trades and candidates to the DB (plan §11.1).

``JournalWriter`` is the only writer of ``trade_journal`` / ``candidate_journal``.
Backtests opt in explicitly (they default to in-memory DataFrames); live/paper/
shadow always journal. Writes are chunked and wrapped in the shared session's
transaction so a crash never leaves a half-written run.
"""
from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
from sqlalchemy import select

from sgt.db.session import get_session
from sgt.engine.models import CandidateJournal, TradeJournal

_CHUNK = 500


def _chunks(rows: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), _CHUNK):
        yield rows[i:i + _CHUNK]


class JournalWriter:
    def __init__(self, run_id: str, mode: str, config_hash: str) -> None:
        if not run_id:
            raise ValueError("JournalWriter requires a non-empty run_id")
        self.run_id = run_id
        self.mode = mode
        self.config_hash = config_hash

    def _tag(self, row: dict[str, Any]) -> dict[str, Any]:
        return {"run_id": self.run_id, "mode": self.mode, "config_hash": self.config_hash, **row}

    def write_trades(self, trades: pd.DataFrame) -> int:
        rows = [self._tag(_coerce_trade(r)) for r in trades.to_dict("records")]
        if not rows:
            return 0
        with get_session() as s:
            for chunk in _chunks(rows):
                s.bulk_insert_mappings(TradeJournal, chunk)
        return len(rows)

    def write_candidates(self, candidates: pd.DataFrame) -> int:
        rows = [self._tag(_coerce_candidate(r)) for r in candidates.to_dict("records")]
        if not rows:
            return 0
        with get_session() as s:
            for chunk in _chunks(rows):
                s.bulk_insert_mappings(CandidateJournal, chunk)
        return len(rows)


def read_recent_closed_trades(config_hash: str, mode: str, limit: int) -> list[dict[str, Any]]:
    """Most-recent closed trades for ``(config_hash, mode)`` in chronological order, for
    decay rehydration on restart (plan §11.4, L-B). Spans run_ids (a restart is a new run).

    Only closed trades (``exit_ts`` set) are returned; each dict carries ``ret``, ``pnl``,
    ``exit_ts`` and ``symbol`` — enough to replay the CUSUM/rolling/drawdown/win-rate
    monitors — plus ``realized_slippage``/``modeled_slippage`` (``None`` on rows that
    predate the slippage columns or on backtests) so the slippage-drift monitor rehydrates
    identically to a no-restart run (plan §11.4, L-B)."""
    if limit <= 0:
        return []
    with get_session() as s:
        stmt = (select(TradeJournal)
                .where(TradeJournal.config_hash == config_hash, TradeJournal.mode == mode,
                       TradeJournal.exit_ts.isnot(None))
                .order_by(TradeJournal.exit_ts.desc(), TradeJournal.id.desc())
                .limit(int(limit)))
        rows = list(s.scalars(stmt))
    rows.reverse()  # oldest-first so cumulative monitors (CUSUM, drawdown) replay in order
    return [{"symbol": r.symbol, "exit_ts": r.exit_ts, "pnl": float(r.pnl),
             "ret": float(r.ret),
             "realized_slippage": (float(r.realized_slippage)
                                   if r.realized_slippage is not None else None),
             "modeled_slippage": (float(r.modeled_slippage)
                                  if r.modeled_slippage is not None else None)}
            for r in rows]


_TRADE_FIELDS = ("symbol", "entry_ts", "entry_price", "exit_ts", "exit_price", "qty", "pnl",
                 "ret", "exit_reason", "mfe", "mae", "bars_held", "halted_during",
                 "realized_slippage", "modeled_slippage")
_CAND_FIELDS = ("symbol", "ts", "taken", "skip_reason", "features")


def _coerce_trade(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: r.get(k) for k in _TRADE_FIELDS}
    out["halted_during"] = bool(out.get("halted_during"))
    out["exit_reason"] = out.get("exit_reason") or ""
    # Slippage is nullable and additive: coerce a present value to float, leave a missing
    # one (backtests / legacy rows) as NULL so the slippage monitor skips it on rehydration.
    for k in ("realized_slippage", "modeled_slippage"):
        out[k] = float(out[k]) if out.get(k) is not None else None
    return out


def _coerce_candidate(r: dict[str, Any]) -> dict[str, Any]:
    feats = r.get("features")
    if not isinstance(feats, dict):
        feats = {}
    return {
        "symbol": r.get("symbol"),
        "ts": r.get("ts"),
        "taken": bool(r.get("taken")),
        "skip_reason": r.get("skip_reason") or "",
        "features": {k: v for k, v in feats.items() if isinstance(v, (int, float))},
    }
