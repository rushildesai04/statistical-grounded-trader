"""Forward-return event study for Telegram alerts (docs/IMPLEMENTATION_PLAN.md §12).

Measures, with no look-ahead, how price behaves after each alert: forward returns
at several horizons, max favorable/adverse excursion over 60 minutes, whether the
name halted within 60 minutes, and an "alert latency" metric (how much of the move
had already happened by entry). Results are grouped (alert type, news/no-news,
optional float bucket, session window) with mean/median, hit rate and a seeded,
deterministic bootstrap 95% CI of the mean.

Entry rule (no look-ahead): entry reference = close of the first COMPLETED bar whose
open is strictly after ``posted_at``. An alert fired mid-bar therefore enters on the
next bar, never the one it landed inside.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from sgt.core.types import Bar

ET = ZoneInfo("America/New_York")

DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 15, 60)
MFE_MAE_WINDOW_MIN = 60
LATENCY_LOOKBACK_MIN = 15
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20261012


@dataclass(frozen=True, slots=True)
class AlertRecord:
    symbol: str
    alert_type: str
    posted_at: datetime
    has_news: bool | None = None
    price: float | None = None


def default_session_classifier(ts: datetime) -> str:
    """Classify an instant into a named ET session window without a calendar dep."""
    et = ts.astimezone(ET)
    minute = et.hour * 60 + et.minute
    if minute < 4 * 60:
        return "overnight"
    if minute < 9 * 60 + 30:
        return "pre"
    if minute < 10 * 60 + 30:
        return "open"
    if minute < 15 * 60:
        return "midday"
    if minute < 16 * 60:
        return "power_hour"
    if minute < 20 * 60:
        return "post"
    return "overnight"


@dataclass(frozen=True, slots=True)
class _Outcome:
    returns: dict[int, float]
    mfe: float | None
    mae: float | None
    halted_60: bool
    latency: float | None


@dataclass(frozen=True, slots=True)
class HorizonStat:
    n: int
    mean: float
    median: float
    hit_rate: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True, slots=True)
class GroupSummary:
    label: str
    n: int
    horizons: dict[int, HorizonStat]
    mfe_mean: float
    mae_mean: float
    halted_rate: float
    latency_mean: float


@dataclass(frozen=True, slots=True)
class EventStudyResult:
    n_alerts: int
    n_with_entry: int
    missing: int
    horizons: tuple[int, ...]
    overall: GroupSummary
    groups: dict[str, GroupSummary] = field(default_factory=dict)

    def to_markdown(self) -> str:
        return _to_markdown(self)


# --------------------------------------------------------------------------- core


def _bar_open_times(bars: Sequence[Bar]) -> list[datetime]:
    return [to_aware(b.ts) for b in bars]


def to_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _compute_outcome(
    alert: AlertRecord,
    bars: Sequence[Bar],
    opens: list[datetime],
    horizons: tuple[int, ...],
    halt_ts: Sequence[datetime],
) -> _Outcome | None:
    posted = to_aware(alert.posted_at)
    # First bar whose OPEN is strictly after the alert (no look-ahead).
    entry_idx = bisect.bisect_right(opens, posted)
    if entry_idx >= len(bars):
        return None
    entry_price = bars[entry_idx].close
    if entry_price <= 0:
        return None

    returns: dict[int, float] = {}
    for h in horizons:
        j = entry_idx + h
        if j < len(bars):
            returns[h] = (bars[j].close - entry_price) / entry_price

    # MFE/MAE over the next MFE_MAE_WINDOW_MIN bars after entry.
    hi = entry_idx + MFE_MAE_WINDOW_MIN
    window = bars[entry_idx + 1 : hi + 1]
    mfe = mae = None
    if window:
        mfe = max((b.high - entry_price) / entry_price for b in window)
        mae = min((b.low - entry_price) / entry_price for b in window)

    # Halt within 60 minutes after the alert (from the alert stream).
    halt_cut = posted + timedelta(minutes=MFE_MAE_WINDOW_MIN)
    lo = bisect.bisect_right(halt_ts, posted)
    halted_60 = lo < len(halt_ts) and halt_ts[lo] <= halt_cut

    # Latency: return from the close ~15 min before the alert to entry.
    ref_time = posted - timedelta(minutes=LATENCY_LOOKBACK_MIN)
    k = bisect.bisect_right(opens, ref_time) - 1
    latency = None
    if 0 <= k < len(bars):
        past = bars[k].close
        if past > 0:
            latency = (entry_price - past) / past

    return _Outcome(returns=returns, mfe=mfe, mae=mae, halted_60=halted_60, latency=latency)


def _bootstrap_ci(values: list[float], rng: np.random.Generator) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return (float("nan"), float("nan"))
    if n == 1:
        return (values[0], values[0])
    arr = np.asarray(values, dtype=float)
    idx = rng.integers(0, n, size=(BOOTSTRAP_SAMPLES, n))
    means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return (float(lo), float(hi))


def _summarize(
    label: str,
    outcomes: list[_Outcome],
    horizons: tuple[int, ...],
    seed: int,
) -> GroupSummary:
    rng = np.random.default_rng(seed)
    hstats: dict[int, HorizonStat] = {}
    for h in horizons:
        vals = [o.returns[h] for o in outcomes if h in o.returns]
        if vals:
            arr = np.asarray(vals, dtype=float)
            ci_low, ci_high = _bootstrap_ci(vals, rng)
            hstats[h] = HorizonStat(
                n=len(vals),
                mean=float(arr.mean()),
                median=float(np.median(arr)),
                hit_rate=float((arr > 0).mean()),
                ci_low=ci_low,
                ci_high=ci_high,
            )
        else:
            hstats[h] = HorizonStat(0, float("nan"), float("nan"), float("nan"),
                                    float("nan"), float("nan"))

    mfes = [o.mfe for o in outcomes if o.mfe is not None]
    maes = [o.mae for o in outcomes if o.mae is not None]
    lats = [o.latency for o in outcomes if o.latency is not None]
    return GroupSummary(
        label=label,
        n=len(outcomes),
        horizons=hstats,
        mfe_mean=float(np.mean(mfes)) if mfes else float("nan"),
        mae_mean=float(np.mean(maes)) if maes else float("nan"),
        halted_rate=(sum(o.halted_60 for o in outcomes) / len(outcomes)) if outcomes else float("nan"),
        latency_mean=float(np.mean(lats)) if lats else float("nan"),
    )


def run_event_study(
    alerts: list[AlertRecord],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    *,
    float_bucket: Mapping[str, str] | None = None,
    classify: Callable[[datetime], str] = default_session_classifier,
    seed: int = BOOTSTRAP_SEED,
) -> EventStudyResult:
    """Run the forward-return study. Alerts with no usable bars are counted as
    missing (never silently dropped from the totals)."""
    if any(h <= 0 for h in horizons):
        raise ValueError("horizons must be positive")
    horizons = tuple(sorted(set(horizons)))

    # Pre-index bars and per-symbol halt timestamps.
    opens_cache: dict[str, list[datetime]] = {}
    halts_cache: dict[str, list[datetime]] = {}
    for sym, bars in bars_by_symbol.items():
        opens_cache[sym] = _bar_open_times(bars)
    for a in alerts:
        if a.alert_type == "halted":
            halts_cache.setdefault(a.symbol, []).append(to_aware(a.posted_at))
    for sym in halts_cache:
        halts_cache[sym].sort()

    tagged: list[tuple[AlertRecord, _Outcome]] = []
    missing = 0
    for a in alerts:
        bars = bars_by_symbol.get(a.symbol)
        if not bars:
            missing += 1
            continue
        outcome = _compute_outcome(
            a, bars, opens_cache[a.symbol], horizons, halts_cache.get(a.symbol, [])
        )
        if outcome is None:
            missing += 1
            continue
        tagged.append((a, outcome))

    all_outcomes = [o for _, o in tagged]
    overall = _summarize("overall", all_outcomes, horizons, seed)

    groups: dict[str, GroupSummary] = {}

    def _add_group(label: str, outcomes: list[_Outcome]) -> None:
        if outcomes:
            groups[label] = _summarize(label, outcomes, horizons, seed)

    # by alert type
    by_type: dict[str, list[_Outcome]] = {}
    for a, o in tagged:
        by_type.setdefault(a.alert_type, []).append(o)
    for key, outs in sorted(by_type.items()):
        _add_group(f"alert_type={key}", outs)

    # by news flag
    by_news: dict[str, list[_Outcome]] = {}
    for a, o in tagged:
        key = "unknown" if a.has_news is None else ("yes" if a.has_news else "no")
        by_news.setdefault(key, []).append(o)
    for key, outs in sorted(by_news.items()):
        _add_group(f"has_news={key}", outs)

    # by float bucket (optional)
    if float_bucket is not None:
        by_float: dict[str, list[_Outcome]] = {}
        for a, o in tagged:
            key = float_bucket.get(a.symbol, "unknown")
            by_float.setdefault(key, []).append(o)
        for key, outs in sorted(by_float.items()):
            _add_group(f"float_bucket={key}", outs)

    # by session window
    by_session: dict[str, list[_Outcome]] = {}
    for a, o in tagged:
        by_session.setdefault(classify(to_aware(a.posted_at)), []).append(o)
    for key, outs in sorted(by_session.items()):
        _add_group(f"session={key}", outs)

    return EventStudyResult(
        n_alerts=len(alerts),
        n_with_entry=len(tagged),
        missing=missing,
        horizons=horizons,
        overall=overall,
        groups=groups,
    )


# --------------------------------------------------------------------------- report


def _pct(x: float) -> str:
    return "n/a" if x != x else f"{x * 100:.2f}%"


def _row(g: GroupSummary, horizons: tuple[int, ...]) -> str:
    cells = [g.label, str(g.n)]
    for h in horizons:
        s = g.horizons[h]
        cells.append(f"{_pct(s.mean)} ({_pct(s.ci_low)}..{_pct(s.ci_high)}) hr {_pct(s.hit_rate)} n={s.n}")
    cells.append(_pct(g.mfe_mean))
    cells.append(_pct(g.mae_mean))
    cells.append(_pct(g.halted_rate))
    cells.append(_pct(g.latency_mean))
    return "| " + " | ".join(cells) + " |"


def _to_markdown(result: EventStudyResult) -> str:
    horizons = result.horizons
    header = ["group", "n"] + [f"ret_{h}m (95% CI)" for h in horizons] + [
        "MFE", "MAE", "halt_60m", "latency_15m"
    ]
    lines = [
        "# Telegram alert event study",
        "",
        f"alerts={result.n_alerts} with_entry={result.n_with_entry} missing={result.missing}",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
        _row(result.overall, horizons),
    ]
    for label in sorted(result.groups):
        lines.append(_row(result.groups[label], horizons))
    return "\n".join(lines)
