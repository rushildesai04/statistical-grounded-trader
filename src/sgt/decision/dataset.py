"""Decision-model dataset builder (docs/DECISION_MODEL.md §10 DATASET CONTRACT).

One streaming pass per symbol over the immutable Parquet bar store: an
:class:`IndicatorBank` is advanced bar by bar (no full backtest), triggers are
evaluated on each flat-symbol bar close, and every firing becomes a candidate row
with its point-in-time features, round-trip cost, ATR-bracket labels for the whole
``(k_tp, k_sl, H)`` grid, and López de Prado uniqueness weights. Discovery is
respected: a symbol-session is admitted only on or after the symbol's
``first_seen_session`` (watchlist symbols always). Output is a Parquet file plus a
JSON sidecar describing the feature set, label grid, cost model and data range.

Memory is bounded: rows are written per symbol through a streaming
:class:`pyarrow.parquet.ParquetWriter`; only one symbol's bars and candidates are
held at a time. The run is deterministic (symbols and sessions sorted, content-only
timestamps/ids).
"""
from __future__ import annotations

import json
import logging
import time as _time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.core.interfaces import ReferenceData
from sgt.core.types import Bar, Segment
from sgt.decision.features import (
    FEATURE_HISTORY,
    FEATURE_NAMES,
    SPEC_ATR,
    CostModelAdapter,
    CostSource,
    FeatureContext,
    compute_features,
)
from sgt.decision.labels import LabelConfig, atr_bracket_label, default_grid, uniqueness_weights
from sgt.decision.triggers import fired_trigger
from sgt.indicators.registry import IndicatorBank, IndicatorContext
from sgt.intraday.calendar import SessionCalendar, default_calendar
from sgt.intraday.store import ParquetBarStore
from sgt.strategy.base import EvalView, HistoryView

log = logging.getLogger(__name__)
_UTC = timezone.utc
_K_ATR = f"{SPEC_ATR.key}.value"

ReferenceFn = Callable[[str, date], ReferenceData | None]
DISCOVERED_UNKNOWN = ""  # discovered_session when no discovery record applies


# --------------------------------------------------------------------------- candidates

@dataclass(slots=True)
class _Candidate:
    signal_idx: int
    ts: datetime
    session_date: date
    trigger: str
    features: dict[str, float]
    atr: float | None


def _indicator_specs():
    from sgt.decision.features import feature_indicator_specs
    from sgt.decision.triggers import trigger_indicator_specs

    seen = {}
    for s in (*feature_indicator_specs(), *trigger_indicator_specs()):
        seen.setdefault(s.key, s)
    return list(seen.values())


def stream_candidates(
    bars: Sequence[Bar],
    *,
    reference_fn: ReferenceFn | None,
    cost_source: CostSource,
    gate_session: date | None,
) -> list[_Candidate]:
    """Stream one symbol's bars, returning a candidate per flat-bar trigger firing on a
    session at/after ``gate_session`` (None = no gate). Point-in-time: a candidate's
    features read only data up to and including its own bar."""
    if not bars:
        return []
    symbol = bars[0].symbol
    cal = default_calendar()
    ref_cache: dict[date, ReferenceData | None] = {}

    def ref_for(d: date) -> ReferenceData | None:
        if reference_fn is None:
            return None
        if d not in ref_cache:
            ref_cache[d] = reference_fn(symbol, d)
        return ref_cache[d]

    first_ref = ref_for(bars[0].session_date)
    bank = IndicatorBank(_indicator_specs(), IndicatorContext(reference=first_ref))

    maxlen = max(FEATURE_HISTORY, 1)
    closes: deque = deque(maxlen=maxlen)
    highs: deque = deque(maxlen=maxlen)
    lows: deque = deque(maxlen=maxlen)
    volumes: deque = deque(maxlen=maxlen)
    snaps: deque = deque(maxlen=maxlen)
    prev_snap: dict | None = None
    session_date = None
    session_high: float | None = None
    premarket_high: float | None = None
    bars_since_high: int | None = None

    out: list[_Candidate] = []
    for idx, bar in enumerate(bars):
        if bar.session_date != session_date:
            session_date = bar.session_date
            session_high = None
            premarket_high = None
            bars_since_high = None
        bank.update(bar)
        snap = bank.snapshot()
        ref = ref_for(bar.session_date)
        view = EvalView(
            bar=bar, snap_now=snap, snap_prev=prev_snap, windows=cal.windows(bar.ts),
            history=HistoryView(list(closes), list(highs), list(lows), list(volumes), list(snaps)),
            position=None, reference=ref,
            session_high_prior=session_high, premarket_high=premarket_high)

        # bars_since_session_high (incl current bar; 0 when this bar sets the high).
        if session_high is None or bar.high >= session_high:
            bars_since_high = 0
        else:
            bars_since_high = (bars_since_high or 0) + 1

        trig = fired_trigger(view)
        if trig is not None and (gate_session is None or bar.session_date >= gate_session):
            try:
                cost_frac, _src = cost_source.round_trip(symbol, bar.ts, bar.close)
            except Exception:  # noqa: BLE001 - degrade the cost feature to NaN, logged
                log.exception("cost lookup failed for %s at %s", symbol, bar.ts)
                cost_frac = None
            fctx = FeatureContext(
                bar=bar, snap=snap, snap_prev=prev_snap,
                history=view.history, reference=ref, cost_frac=cost_frac,
                session_high_prior=session_high, premarket_high=premarket_high,
                bars_since_session_high=bars_since_high, trigger=trig, windows=view.windows)
            atr = snap.get(_K_ATR)
            out.append(_Candidate(
                signal_idx=idx, ts=bar.ts, session_date=bar.session_date, trigger=trig,
                features=compute_features(fctx),
                atr=atr if (atr is not None and atr > 0.0) else None))

        # advance history / session trackers AFTER the decision (point-in-time).
        closes.append(bar.close); highs.append(bar.high); lows.append(bar.low)
        volumes.append(bar.volume); snaps.append(snap)
        prev_snap = snap
        if bar.segment in (Segment.PRE, Segment.RTH):
            session_high = bar.high if session_high is None else max(session_high, bar.high)
        if bar.segment is Segment.PRE:
            premarket_high = bar.high if premarket_high is None else max(premarket_high, bar.high)
    return out


# --------------------------------------------------------------------------- labels per symbol

def _label_symbol(
    bars: Sequence[Bar], cands: list[_Candidate], grid: Sequence[LabelConfig],
    cost_source: CostSource,
) -> dict[str, list[float]]:
    """Per config: net return, bars-held and uniqueness-weight columns for every
    candidate (NaN where truncated). Uniqueness is computed within each config over the
    non-truncated label spans for this symbol."""
    n = len(bars)
    cols: dict[str, list[float]] = {}
    symbol = bars[0].symbol if bars else ""
    for cfg in grid:
        suf = cfg.suffix
        net_col: list[float] = []
        bar_col: list[float] = []
        spans: list[tuple[int, int]] = []
        span_rows: list[int] = []
        for row, cand in enumerate(cands):
            half = _entry_half(bars, cand.signal_idx, symbol, cost_source)
            outc = atr_bracket_label(bars, cand.signal_idx, cand.atr, half, cfg)
            if outc is None:
                net_col.append(float("nan"))
                bar_col.append(float("nan"))
            else:
                net_col.append(outc.net_ret)
                bar_col.append(float(outc.bars_held))
                spans.append((outc.entry_idx, outc.exit_idx))
                span_rows.append(row)
        uw = uniqueness_weights(spans, n)
        uniq_col = [float("nan")] * len(cands)
        for k, row in enumerate(span_rows):
            uniq_col[row] = uw[k]
        cols[f"y_net__{suf}"] = net_col
        cols[f"y_exit_bar__{suf}"] = bar_col
        cols[f"uniq_w__{suf}"] = uniq_col
    return cols


def _entry_half(bars, signal_idx, symbol, cost_source) -> float:
    """Per-side cost fraction at the entry bar (round-trip / 2)."""
    i = signal_idx + 1
    if i < len(bars) and bars[i].session_date == bars[signal_idx].session_date:
        b = bars[i]
        price, ts = b.open, b.ts
    else:
        b = bars[signal_idx]
        price, ts = b.close, b.ts
    try:
        frac, _src = cost_source.round_trip(symbol, ts, price)
    except Exception:  # noqa: BLE001
        return 0.0
    return frac / 2.0


def _cost_cols(bars, cand, symbol, cost_source, model_source) -> tuple[float, float, str]:
    """(cost_rt_measured, cost_rt_model, cost_source) at the entry bar."""
    i = cand.signal_idx + 1
    if i < len(bars) and bars[i].session_date == cand.session_date:
        price, ts = bars[i].open, bars[i].ts
    else:
        price, ts = bars[cand.signal_idx].close, cand.ts
    frac, source = cost_source.round_trip(symbol, ts, price)
    model_frac, _ = model_source.round_trip(symbol, ts, price)
    measured = frac if source == "measured" else float("nan")
    return measured, model_frac, source


# --------------------------------------------------------------------------- discovery

def discovery_map(
    symbols: Sequence[str], watchlist: Sequence[str],
) -> dict[str, date | None]:
    """symbol -> gate session (inclusive) or None (no gate). Tracked symbols gate on
    ``first_seen_session``; watchlist symbols never gate."""
    from sgt.db.session import get_session
    from sgt.intraday.models import TrackedSymbolRow

    wl = {s.upper() for s in watchlist}
    out: dict[str, date | None] = {}
    want = {s.upper() for s in symbols}
    with get_session() as session:
        for row in session.query(TrackedSymbolRow).filter(
                TrackedSymbolRow.symbol.in_(list(want))).all():
            out[row.symbol.upper()] = date.fromisoformat(row.first_seen_session)
    for s in want:
        if s in wl:
            out[s] = None
    return out


# --------------------------------------------------------------------------- schema / build

def _arrow_schema(grid: Sequence[LabelConfig]) -> pa.Schema:
    fields = [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("session_date", pa.string(), nullable=False),
        pa.field("trigger", pa.string(), nullable=False),
    ]
    fields += [pa.field(f, pa.float64()) for f in FEATURE_NAMES]
    fields.append(pa.field("q_float_degraded", pa.float64()))
    fields += [pa.field("cost_rt_measured", pa.float64()),
               pa.field("cost_rt_model", pa.float64()),
               pa.field("cost_source", pa.string(), nullable=False)]
    for cfg in grid:
        suf = cfg.suffix
        fields += [pa.field(f"y_net__{suf}", pa.float64()),
                   pa.field(f"y_exit_bar__{suf}", pa.float64()),
                   pa.field(f"uniq_w__{suf}", pa.float64())]
    fields.append(pa.field("discovered_session", pa.string(), nullable=False))
    return pa.schema(fields)


@dataclass(slots=True)
class BuildSummary:
    dataset_id: str
    out_path: str
    rows: int
    symbols: int
    sessions: int
    truncated_labels: int
    elapsed_s: float


def build_dataset(
    *,
    store_root: str | Path = "data/alpaca",
    out_dir: str | Path = "data/decision",
    dataset_id: str | None = None,
    watchlist: Sequence[str] = (),
    grid: Sequence[LabelConfig] | None = None,
    cost_source: CostSource | None = None,
    reference_fn: ReferenceFn | None = None,
    symbols: Sequence[str] | None = None,
    research_ungated: bool = False,
) -> BuildSummary:
    """Build the decision dataset from the Parquet store. Returns a summary; writes
    ``<out_dir>/<dataset_id>/dataset.parquet`` and ``meta.json``."""
    t0 = _time.monotonic()
    grid = tuple(grid) if grid is not None else default_grid()
    store = ParquetBarStore(store_root)
    if cost_source is None:
        cost_source = CostModelAdapter(DEFAULT_COST_MODEL)
    model_source = CostModelAdapter(DEFAULT_COST_MODEL)
    if reference_fn is None:
        reference_fn = _default_reference_fn()

    all_symbols = sorted(symbols) if symbols is not None else store.symbols()
    # research_ungated: admit sessions before each symbol's discovery. Those symbols were
    # chosen BECAUSE they moved later, so the sample is momentum-biased: hypothesis
    # generation only; the sidecar marks it promotion-ineligible (2026-10-17 ruling).
    disco = {} if research_ungated else discovery_map(all_symbols, watchlist)
    dataset_id = dataset_id or datetime.now(_UTC).strftime("decision_%Y%m%dT%H%M%SZ")
    out_path = Path(out_dir) / dataset_id
    out_path.mkdir(parents=True, exist_ok=True)
    pq_path = out_path / "dataset.parquet"
    schema = _arrow_schema(grid)

    rows_total = 0
    sessions_seen: set[date] = set()
    truncated = 0
    symbols_with_rows = 0
    writer: pq.ParquetWriter | None = None
    try:
        for sym in all_symbols:
            sessions = store.sessions(sym)
            if not sessions:
                continue
            gate = disco.get(sym, None)
            discovered = (gate.isoformat() if gate is not None
                          else (sessions[0].isoformat() if sym.upper() in {w.upper() for w in watchlist}
                                else DISCOVERED_UNKNOWN))
            bars = store.read(sym, sessions[0], sessions[-1])
            cands = stream_candidates(
                bars, reference_fn=reference_fn, cost_source=cost_source, gate_session=gate)
            if not cands:
                continue
            label_cols = _label_symbol(bars, cands, grid, cost_source)
            table = _symbol_table(sym, bars, cands, label_cols, grid, cost_source,
                                  model_source, discovered, schema)
            truncated += sum(int(v != v) for v in label_cols[f"y_net__{grid[0].suffix}"])
            if writer is None:
                writer = pq.ParquetWriter(pq_path, schema)
            writer.write_table(table)
            rows_total += table.num_rows
            symbols_with_rows += 1
            sessions_seen.update(c.session_date for c in cands)
            log.info("decision-dataset: %s -> %d rows (%d sessions)",
                     sym, table.num_rows, len({c.session_date for c in cands}))
    finally:
        if writer is not None:
            writer.close()
    if writer is None:  # no rows at all: still emit an empty file for a stable contract
        pq.write_table(schema.empty_table(), pq_path)

    elapsed = _time.monotonic() - t0
    _write_sidecar(out_path, dataset_id, grid, store_root, rows_total, symbols_with_rows,
                   sorted(sessions_seen), cost_source, research_ungated)
    return BuildSummary(dataset_id, str(pq_path), rows_total, symbols_with_rows,
                        len(sessions_seen), truncated, elapsed)


def _symbol_table(sym, bars, cands, label_cols, grid, cost_source, model_source,
                  discovered, schema) -> pa.Table:
    n = len(cands)
    data: dict[str, list] = {
        "symbol": [sym] * n,
        "ts": [c.ts for c in cands],
        "session_date": [c.session_date.isoformat() for c in cands],
        "trigger": [c.trigger for c in cands],
    }
    for f in FEATURE_NAMES:
        data[f] = [c.features.get(f, float("nan")) for c in cands]
    data["q_float_degraded"] = [c.features.get("q_float_degraded", float("nan")) for c in cands]
    measured, model_c, source_c = [], [], []
    for c in cands:
        m, mo, s = _cost_cols(bars, c, sym, cost_source, model_source)
        measured.append(m); model_c.append(mo); source_c.append(s)
    data["cost_rt_measured"] = measured
    data["cost_rt_model"] = model_c
    data["cost_source"] = source_c
    for cfg in grid:
        suf = cfg.suffix
        data[f"y_net__{suf}"] = label_cols[f"y_net__{suf}"]
        data[f"y_exit_bar__{suf}"] = label_cols[f"y_exit_bar__{suf}"]
        data[f"uniq_w__{suf}"] = label_cols[f"uniq_w__{suf}"]
    data["discovered_session"] = [discovered] * n
    return pa.table(data, schema=schema)


def _default_reference_fn() -> ReferenceFn:
    from sgt.catalysts.reference import ReferenceDataProvider

    provider = ReferenceDataProvider()
    return lambda sym, d: provider.get(sym, d)


def _write_sidecar(out_path, dataset_id, grid, store_root, rows, n_symbols, sessions,
                   cost_source, research_ungated: bool = False) -> None:
    meta = {
        "dataset_id": dataset_id,
        "created_utc": datetime.now(_UTC).isoformat(),
        "source_store": str(store_root),
        "rows": rows,
        "symbols": n_symbols,
        "sessions": [d.isoformat() for d in sessions],
        "data_range": ([sessions[0].isoformat(), sessions[-1].isoformat()] if sessions else []),
        "feature_names": list(FEATURE_NAMES),
        "quality_flags": ["q_float_degraded"],
        "label_grid": [{"k_tp": c.k_tp, "k_sl": c.k_sl, "h": c.h, "suffix": c.suffix}
                       for c in grid],
        "cost_model": type(cost_source).__name__,
        "research_ungated": research_ungated,
        "promotion_eligible": not research_ungated,
    }
    (out_path / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
