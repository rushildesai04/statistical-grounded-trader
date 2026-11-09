"""Forward paper test of the daily per-ticker models (prereg_megacap_forward_2026-10-30).

Virtual ledger only: no orders. ``train_month`` fits each ticker's model on all rows before the month;
``decide`` writes today's positions at 15:50 ET (append-only); ``mark`` fills them at official closes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.megacap.daily import DAILY_FEATURES, build_daily
from sgt.megacap.walkforward import Q_HIGH, Q_LOW

ET = "America/New_York"
TICKERS = ("AAPL", "AMZN", "GOOGL", "MSFT", "META", "NVDA")
# Extended forward set (prereg_megacap_ext_forward_2026-11-09): the other 15 names on the OOS record
# (config/models/model_tape.json) that are not in TICKERS, in tape order. Frozen for the pre-registration;
# tests/megacap/test_ext.py guards that this equals the tape minus TICKERS.
EXT_TICKERS = ("RDDT", "TSLA", "AMD", "AVGO", "COST", "JPM", "LLY", "MA", "NFLX", "ORCL", "UNH", "V", "WMT",
               "XOM", "AAOI")
COST_RT = 0.0004
PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=7, min_child_samples=100,
              colsample_bytree=0.8, subsample=0.8, subsample_freq=1, random_state=0, verbose=-1)
ROOT = Path("reports/megacap_forward")
ROOT_EXT = Path("reports/megacap_ext")
VOID_AFTER = (15, 55)          # a decision written later than 15:55 ET is void
TIMEOUT_S = 20.0


class LiveError(RuntimeError):
    pass


# ---------------------------------------------------------------------------- data
def alpaca_bars(symbols: list[str], timeframe: str, start: str, end: str | None, *, feed: str,
                adjustment: str = "raw", headers: dict | None = None, base_url: str | None = None) -> dict[str, pd.DataFrame]:
    """Paginated Alpaca bars -> {symbol: frame(ts, open, high, low, close, volume)} (bounded, fail closed)."""
    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    url = (base_url or get_settings().alpaca_data_url).rstrip("/") + "/v2/stocks/bars"
    params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "feed": feed,
              "adjustment": adjustment, "limit": 10000}
    if end:
        params["end"] = end
    out: dict[str, list] = {s: [] for s in symbols}
    for _ in range(200):
        r = httpx.get(url, params=params, headers=headers or _alpaca_headers(), timeout=TIMEOUT_S)
        if r.status_code >= 400:
            raise LiveError(f"alpaca bars HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        for s, rows in (j.get("bars") or {}).items():
            out.setdefault(s, []).extend(rows)
        tok = j.get("next_page_token")
        if not tok:
            break
        params["page_token"] = tok
    else:
        raise LiveError("alpaca bars: pagination did not finish")
    frames = {}
    for s, rows in out.items():
        f = pd.DataFrame(rows)
        if f.empty:
            frames[s] = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
            continue
        f = f.rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        f["ts"] = pd.to_datetime(f.ts, utc=True)
        frames[s] = f[["ts", "open", "high", "low", "close", "volume"]].sort_values("ts").reset_index(drop=True)
    return frames


def to_daily(f: pd.DataFrame) -> pd.DataFrame:
    d = f.copy()
    d.index = d.ts.dt.tz_convert(ET).dt.date
    return d[["open", "high", "low", "close", "volume"]]


# ---------------------------------------------------------------------------- models
# ``root``/``tickers`` default to the original six-name forward test (ROOT/TICKERS) and are resolved at
# call time so the existing monkeypatch of ``live.ROOT`` in tests keeps working; the extended set
# (prereg_megacap_ext_forward_2026-11-09) passes ROOT_EXT / EXT_TICKERS to reuse this code unchanged.
def model_dir(month: str, root: Path | None = None) -> Path:
    return (ROOT if root is None else root) / "models" / month


def train_month(month: str, features_by_symbol: dict[str, pd.DataFrame], *, root: Path | None = None) -> dict:
    """Fit each ticker on rows with session < month start; save booster + thresholds."""
    import lightgbm as lgb

    start = pd.Period(month, "M").start_time.date()
    out_dir = model_dir(month, root)
    if (out_dir / "meta.json").exists():
        raise LiveError(f"models for {month} already exist (append-only)")
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"month": month, "trained_at": datetime.now(timezone.utc).isoformat(), "tickers": {}}
    for s, df in features_by_symbol.items():
        tr = df[(pd.to_datetime(df.session).dt.date < start) & df.label.notna()]
        y = tr.label.to_numpy(float)
        lo, hi = np.quantile(y, [0.01, 0.99])
        m = lgb.LGBMRegressor(**PARAMS).fit(tr[list(DAILY_FEATURES)], np.clip(y, lo, hi))
        ins = m.predict(tr[list(DAILY_FEATURES)])
        q_lo, q_hi = (float(x) for x in np.quantile(ins, [Q_LOW, Q_HIGH]))
        m.booster_.save_model(str(out_dir / f"{s}.txt"))
        sha = hashlib.sha256((out_dir / f"{s}.txt").read_bytes()).hexdigest()
        meta["tickers"][s] = {"rows": int(len(tr)), "last_session": str(tr.session.max()), "q_lo": q_lo, "q_hi": q_hi,
                              "sha256": sha}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def load_models(month: str, *, root: Path | None = None) -> tuple[dict, dict]:
    import lightgbm as lgb

    d = model_dir(month, root)
    if not (d / "meta.json").exists():
        raise LiveError(f"no models for {month}; run retrain first")
    meta = json.loads((d / "meta.json").read_text())
    for s, m in meta["tickers"].items():
        if "sha256" in m and hashlib.sha256((d / f"{s}.txt").read_bytes()).hexdigest() != m["sha256"]:
            raise LiveError(f"{month}/{s}: model file does not match its recorded hash")
    return {s: lgb.Booster(model_file=str(d / f"{s}.txt")) for s in meta["tickers"]}, meta


# ---------------------------------------------------------------------------- decisions
def today_row(today: date, intraday: dict[str, pd.DataFrame], daily: dict[str, pd.DataFrame], sym: str) -> pd.Series:
    """Feature row for ``today`` from daily bars through yesterday + today's bars to 15:49 ET.

    ``daily`` must include TODAY's partial adjusted SIP daily bar: its open is the official opening
    price the backtest used (the first 1-minute trade can differ by ~1%; parity check 2026-10-30)."""
    def with_today(s: str) -> pd.DataFrame:
        if today not in daily[s].index:
            raise LiveError(f"{s}: today's daily bar (official open) unavailable; fail closed")
        if intraday[s].empty:
            raise LiveError(f"{s}: no intraday bars today")
        d = daily[s][daily[s].index < today]
        row = pd.DataFrame({"open": [float(daily[s].loc[today].open)], "high": [np.nan], "low": [np.nan],
                            "close": [np.nan], "volume": [np.nan]}, index=[today])
        return pd.concat([d, row])

    rows = build_daily(intraday[sym], with_today(sym), intraday["SPY"], with_today("SPY"), intraday["QQQ"],
                       with_today("QQQ"), require_label=False, live_session=today)
    r = rows[rows.session == today]
    if r.empty:
        raise LiveError(f"{sym}: could not build today's row")
    return r.iloc[0]


def decide(today: date, now_et: datetime, intraday: dict, daily: dict, *, mode: str = "realtime",
           tickers: tuple[str, ...] | None = None, root: Path | None = None) -> Path:
    """``realtime``: written at 15:50 ET, void after 15:55. ``batch`` (2026-10-30, forward amendment A1):
    computed later from the same point-in-time data (bars ending 15:49, frozen month model); proof comes
    from the hashed models + tested point-in-time code, not the clock."""
    if mode not in ("realtime", "batch"):
        raise LiveError(f"unknown mode {mode}")
    if mode == "realtime" and (now_et.hour, now_et.minute) > VOID_AFTER:
        raise LiveError(f"too late ({now_et:%H:%M} ET): decisions after 15:55 are void")
    syms = TICKERS if tickers is None else tickers
    root = ROOT if root is None else root
    path = root / "decisions" / f"{today}.json"
    if path.exists():
        raise LiveError(f"{path} exists (append-only)")
    models, meta = load_models(f"{today:%Y-%m}", root=root)
    out = {"date": str(today), "written_at": datetime.now(timezone.utc).isoformat(), "mode": mode,
           "model_month": meta["month"], "positions": {}}
    for s in syms:
        x = today_row(today, intraday, daily, s)
        pred = float(models[s].predict(pd.DataFrame([x[list(DAILY_FEATURES)].astype(float)]))[0])
        th = meta["tickers"][s]
        pos = 1 if pred >= th["q_hi"] else (-1 if pred <= th["q_lo"] else 0)
        out["positions"][s] = {"pred": pred, "position": pos, "q_lo": th["q_lo"], "q_hi": th["q_hi"],
                               "features": {f: (None if pd.isna(x[f]) else float(x[f])) for f in DAILY_FEATURES}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))
    return path


def mark(daily: dict[str, pd.DataFrame], *, root: Path | None = None) -> pd.DataFrame:
    """Fill every valid decision at official closes t -> t+1 (adjusted daily bars), net of cost."""
    root = ROOT if root is None else root
    rows = []
    for p in sorted((root / "decisions").glob("*.json")):
        dec = json.loads(p.read_text())
        t = date.fromisoformat(dec["date"])
        written = pd.Timestamp(dec["written_at"]).tz_convert(ET)
        realtime = dec.get("mode", "realtime") == "realtime"
        void = realtime and ((written.date() != t) or ((written.hour, written.minute) > VOID_AFTER))
        for s, v in dec["positions"].items():
            d = daily[s]
            if t not in d.index:
                continue
            i = d.index.get_loc(t)
            if i + 1 >= len(d):
                continue  # next close not yet known
            ret = float(d.close.iloc[i + 1] / d.close.iloc[i] - 1)
            pos = 0 if void else int(v["position"])
            rows.append({"date": t, "symbol": s, "position": pos, "ret": ret, "void": void,
                         "net": pos * ret - (COST_RT if pos else 0.0)})
    return pd.DataFrame(rows)
