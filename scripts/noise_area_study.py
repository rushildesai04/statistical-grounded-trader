"""Intraday momentum on SPY: the published "noise area" rule (docs/findings/prereg_intraday_momentum_2026-11-19.md).

    .venv/bin/python scripts/noise_area_study.py replicate   # step 1, the replication gate, run ONCE
    .venv/bin/python scripts/noise_area_study.py score       # step 2, the out-of-sample decision, ONCE,
                                                             # only after the gate passed (vault unlock)

Report only: nothing here changes a book. The rule is the paper's (Zarattini, Aziz, Barbon 2024); the held-out period
is guarded by the holdout vault (study ``intraday-momentum-2026-11``)."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

STUDY = "intraday-momentum-2026-11"
SCORER = "scripts/noise_area_study.py"
PREREG = "docs/findings/prereg_intraday_momentum_2026-11-19.md"
OUT = Path("reports/noise_area")
FIRST_DATA, REPL_FROM, REPL_TO = date(2016, 1, 4), date(2016, 1, 25), date(2024, 4, 30)
OOS_FROM, OOS_TO = date(2024, 5, 1), date(2026, 10, 30)
OPEN_MIN, DAY_MIN = 570, 390                 # 09:30 ET; minutes in a full session
LOOKBACK, MIN_DAYS = 14, 10                  # sessions in the average move; a time needs this many of them
STEP = 30                                    # decisions at HH:00 and HH:30
COSTS = {"paper": 0.0045, "careful": 0.02}   # dollars per share, each side
START_VALUE = 100_000.0
VOL_TARGET, MAX_LEV = 0.02, 4.0              # the paper's volatility-targeted size (reported only)
GATE_TRADES = (1.3, 2.3)
BLOCK, ALPHA, SEED, N_BOOT = 5, 0.05, 20261119, 10_000


# --- one instrument's sessions as arrays ---------------------------------------------------------------------------

def sessions_from_bars(f: pd.DataFrame) -> dict:
    """Regular-session bars (``session_date``, ``minute_of_day``, open, high, low, close, volume, vwap; a bar is named
    by its START) -> per session: P[k] = the price at minute k of the session (P[0] = the 09:30 open, P[k] = the
    close of the last bar that ended by then; NaN after the session's last bar), V[k] = the running VWAP of the bars
    that ended by minute k, O[k] = the open of the first bar starting at or after minute k (the next-bar fill),
    last = the session's last minute. A missing minute never invents a price: the last one stands."""
    f = f[f.segment == "rth"] if "segment" in f else f
    days = sorted(f.session_date.unique())
    n = len(days)
    P, V, O = np.full((n, DAY_MIN + 1), np.nan), np.full((n, DAY_MIN + 1), np.nan), np.full((n, DAY_MIN + 1), np.nan)
    last = np.zeros(n, int)
    for j, (_, g) in enumerate(f.groupby("session_date", sort=True)):
        i = g.minute_of_day.to_numpy(int) - OPEN_MIN
        ok = (i >= 0) & (i < DAY_MIN)
        g, i = g[ok], i[ok]
        order = np.argsort(i, kind="stable")
        g, i = g.iloc[order], i[order]
        if not len(i):
            continue
        o, c, vol = g.open.to_numpy(float), g.close.to_numpy(float), g.volume.to_numpy(float)
        typ = (g.high.to_numpy(float) + g.low.to_numpy(float) + c) / 3
        bv = g.vwap.to_numpy(float) if "vwap" in g else typ
        bv = np.where(np.isfinite(bv) & (bv > 0), bv, typ)
        end = int(i[-1]) + 1
        last[j] = end
        close_at = np.full(DAY_MIN + 1, np.nan)
        close_at[i + 1] = c
        P[j, 0] = o[0]
        P[j, 1:end + 1] = pd.Series(close_at[1:end + 1]).ffill().to_numpy()
        P[j, 1:end + 1] = np.where(np.isfinite(P[j, 1:end + 1]), P[j, 1:end + 1], o[0])
        pv, vv = np.zeros(DAY_MIN + 1), np.zeros(DAY_MIN + 1)
        pv[i + 1], vv[i + 1] = bv * vol, vol
        cpv, cvv = np.cumsum(pv), np.cumsum(vv)
        with np.errstate(invalid="ignore", divide="ignore"):
            vw = np.where(cvv > 0, cpv / cvv, np.nan)
        V[j, 1:end + 1] = vw[1:end + 1]
        open_at = np.full(DAY_MIN + 1, np.nan)
        open_at[i] = o
        O[j, :end] = pd.Series(open_at[:end]).bfill().to_numpy()
        O[j, :end] = np.where(np.isfinite(O[j, :end]), O[j, :end], c[-1])
    return {"days": np.array([str(d)[:10] for d in days]), "P": P, "V": V, "O": O, "last": last}


def boundaries(s: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(sigma, upper, lower), each sessions x minutes. sigma[t, k] = the mean absolute move from the open to minute k
    over the PREVIOUS ``LOOKBACK`` sessions (at least ``MIN_DAYS`` of them reaching minute k); upper / lower from
    today's open and the previous session's last close. Nothing of session t beyond its open is used."""
    P, last = s["P"], s["last"]
    n = len(P)
    with np.errstate(invalid="ignore", divide="ignore"):
        move = np.abs(P / P[:, [0]] - 1)
    sigma = np.full_like(P, np.nan)
    for t in range(LOOKBACK, n):
        w = move[t - LOOKBACK:t]
        cnt = np.isfinite(w).sum(0)
        with np.errstate(invalid="ignore"):
            m = np.nansum(w, 0) / np.where(cnt > 0, cnt, 1)
        sigma[t] = np.where(cnt >= MIN_DAYS, m, np.nan)
    prev = np.full(n, np.nan)
    for t in range(1, n):
        prev[t] = P[t - 1, last[t - 1]] if last[t - 1] > 0 else np.nan
    hi, lo = np.fmax(P[:, 0], prev), np.fmin(P[:, 0], prev)
    upper = hi[:, None] * (1 + sigma)
    lower = lo[:, None] * (1 - sigma)
    upper[~np.isfinite(prev)] = np.nan
    lower[~np.isfinite(prev)] = np.nan
    return sigma, upper, lower


# --- the rule ------------------------------------------------------------------------------------------------------

def day_trades(P: np.ndarray, V: np.ndarray, O: np.ndarray, U: np.ndarray, L: np.ndarray, last: int, rule: str,
               fill: str) -> list[dict]:
    """One session's round trips under ``rule`` ("B" = the band and VWAP trailing stop, "A" = the base rule) with
    ``fill`` ("next" = the open of the bar starting at the decision minute, "decision" = the decision price).
    Each: side, entry / exit minute, entry / exit price. Decisions only at half-hour marks before the close; every
    position is closed at the session's last price."""
    out, pos, entry_k, entry_px = [], 0, 0, 0.0
    for k in range(STEP, last, STEP):
        p, u, lo_, v = P[k], U[k], L[k], V[k]
        if not (np.isfinite(p) and np.isfinite(u) and np.isfinite(lo_)):
            continue
        if rule == "B":
            if not np.isfinite(v):
                continue
            top, bot = max(u, v), min(lo_, v)
            want = 1 if p > top else -1 if p < bot else (pos if (pos == 1 and p == top) or (pos == -1 and p == bot) else 0)
        else:
            want = 1 if p > u else -1 if p < lo_ else pos
        if want == pos:
            continue
        px = float(O[k]) if fill == "next" else float(p)
        if pos != 0:
            out.append({"side": pos, "in_k": entry_k, "out_k": k, "entry": entry_px, "exit": px})
        pos, entry_k, entry_px = want, k, px
    if pos != 0:
        out.append({"side": pos, "in_k": entry_k, "out_k": last, "entry": entry_px, "exit": float(P[last])})
    return out


def simulate(s: dict, rule: str = "B", fill: str = "next", cost: float = COSTS["careful"], sized: str = "full",
             start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """The book day by day over sessions ``start`` .. ``end`` (inclusive; the sessions before them only feed the
    boundaries). Per session: ret (net, on the value at the previous close), gross, trades, wins, and the fund's own
    close-to-close return. Size: shares = floor(value x leverage / today's open), leverage 1 ("full") or the paper's
    volatility target ("vol"). Every fill pays ``cost`` a share."""
    _, U, L = boundaries(s)
    P, V, O, last, days = s["P"], s["V"], s["O"], s["last"], s["days"]
    closes = np.array([P[t, last[t]] if last[t] > 0 else np.nan for t in range(len(P))])
    with np.errstate(invalid="ignore", divide="ignore"):
        cc = np.r_[np.nan, closes[1:] / closes[:-1] - 1]
    value, rows = START_VALUE, []
    for t in range(len(P)):
        if (start and days[t] < start) or (end and days[t] > end) or last[t] <= STEP or not np.isfinite(P[t, 0]):
            continue
        lev = 1.0
        if sized == "vol":
            w = cc[max(0, t - LOOKBACK):t]
            w = w[np.isfinite(w)]
            sd = float(np.std(w, ddof=1)) if len(w) >= MIN_DAYS else np.nan
            lev = min(MAX_LEV, VOL_TARGET / sd) if sd and np.isfinite(sd) and sd > 0 else 0.0
        shares = np.floor(value * lev / P[t, 0])
        trades = day_trades(P[t], V[t], O[t], U[t], L[t], int(last[t]), rule, fill)
        gross = sum(tr["side"] * (tr["exit"] - tr["entry"]) for tr in trades) * shares
        fees = 2 * len(trades) * shares * cost
        wins = sum(tr["side"] * (tr["exit"] - tr["entry"]) - 2 * cost > 0 for tr in trades)
        rows.append({"session": days[t], "ret": (gross - fees) / value, "gross": gross / value, "trades": len(trades),
                     "wins": int(wins), "longs": sum(tr["side"] == 1 for tr in trades), "fund": cc[t],
                     "long_ret": sum((tr["exit"] - tr["entry"] - 2 * cost) for tr in trades if tr["side"] == 1) * shares / value,
                     "short_ret": sum((tr["entry"] - tr["exit"] - 2 * cost) for tr in trades if tr["side"] == -1) * shares / value})
        value *= 1 + rows[-1]["ret"]
    return pd.DataFrame(rows)


# --- statistics ----------------------------------------------------------------------------------------------------

def lower_bound(x: np.ndarray, alpha: float = ALPHA) -> float | None:
    """One-sided lower bound of the mean of a daily series: consecutive blocks of ``BLOCK`` sessions resampled with
    replacement (the series keeps its order inside a block). None when there are fewer than two blocks."""
    x = np.asarray(x, float)
    nb = int(np.ceil(len(x) / BLOCK))
    if nb < 2:
        return None
    sums = np.array([x[i * BLOCK:(i + 1) * BLOCK].sum() for i in range(nb)])
    cnts = np.array([len(x[i * BLOCK:(i + 1) * BLOCK]) for i in range(nb)], float)
    idx = np.random.default_rng(SEED).integers(0, nb, size=(N_BOOT, nb))
    return float(np.quantile(sums[idx].sum(1) / cnts[idx].sum(1), alpha))


def describe(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"sessions": 0}
    r = d.ret.to_numpy(float)
    eq = np.cumprod(1 + r)
    years = len(r) / 252
    traded = d[d.trades > 0]
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    fund = d.fund.to_numpy(float)
    ok = np.isfinite(fund)
    return {"sessions": int(len(r)), "mean_daily": float(r.mean()), "total_return": float(eq[-1] - 1),
            "annual_return": float(eq[-1] ** (1 / years) - 1) if years > 0 and eq[-1] > 0 else None,
            "annual_vol": sd * np.sqrt(252), "sharpe": float(r.mean() / sd * np.sqrt(252)) if sd > 0 else None,
            "worst_day": float(r.min()), "max_drawdown": float((eq / np.maximum.accumulate(eq) - 1).min()),
            "trades": int(d.trades.sum()), "trades_per_day": float(d.trades.sum() / len(d)),
            "trade_win_share": float(d.wins.sum() / d.trades.sum()) if d.trades.sum() else None,
            "day_win_share_traded": float((traded.ret > 0).mean()) if len(traded) else None,
            "day_win_share_all": float((r > 0).mean()), "long_share_of_trades": float(d.longs.sum() / d.trades.sum()) if d.trades.sum() else None,
            "long_mean_daily": float(d.long_ret.mean()), "short_mean_daily": float(d.short_ret.mean()),
            "corr_with_fund": float(np.corrcoef(r[ok], fund[ok])[0, 1]) if ok.sum() > 2 and sd > 0 else None}


def gate(rep: dict) -> dict:
    """The replication gate: the rule's mechanics (trades a day) and its sign, on the paper's own setting."""
    tpd, tot = rep.get("trades_per_day"), rep.get("total_return")
    ok_t = tpd is not None and GATE_TRADES[0] <= tpd <= GATE_TRADES[1]
    ok_s = tot is not None and tot > 0
    return {"trades_per_day": tpd, "trades_ok": bool(ok_t), "total_return": tot, "sign_ok": bool(ok_s), "pass": bool(ok_t and ok_s)}


def decide(d: pd.DataFrame) -> dict:
    lb = lower_bound(d.ret.to_numpy(float))
    m = float(d.ret.mean()) if len(d) else None
    verdict = "FAIL" if m is None or m <= 0 else ("PASS" if lb is not None and lb > 0 else "LEAD")
    return {"mean_daily": m, "lower_bound": lb, "alpha": ALPHA, "verdict": verdict}


# --- running it ----------------------------------------------------------------------------------------------------

def load(vault, store, symbol: str, start: date, end: date) -> dict:
    parts = [vault.read(store, symbol, max(start, date(y, 1, 1)), min(end, date(y, 12, 31))) for y in range(start.year, end.year + 1)]
    f = pd.concat([p for p in parts if len(p)], ignore_index=True)
    return sessions_from_bars(f)


def replicate(vault, store, out: Path) -> dict:
    s = load(vault, store, "SPY", FIRST_DATA, REPL_TO)
    a, b = REPL_FROM.isoformat(), REPL_TO.isoformat()
    paper = describe(simulate(s, "B", "decision", COSTS["paper"], start=a, end=b))
    res = {"prereg": PREREG, "step": "replication", "span": [a, b], "gate": gate(paper), "paper_setting": paper,
           "decision_cell_on_replication_period": describe(simulate(s, "B", "next", COSTS["careful"], start=a, end=b)),
           "rule_A_paper_setting": describe(simulate(s, "A", "decision", COSTS["paper"], start=a, end=b)),
           "by_year_paper_setting": {y: describe(g) for y, g in simulate(s, "B", "decision", COSTS["paper"], start=a, end=b)
                                     .assign(y=lambda d: d.session.str[:4]).groupby("y")}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "result_replication.json").write_text(json.dumps(res, indent=1) + "\n")
    return res


def score(vault, store, out: Path) -> dict:
    vault.unlock(SCORER)
    a, b = OOS_FROM.isoformat(), OOS_TO.isoformat()
    res: dict = {"prereg": PREREG, "step": "out-of-sample", "span": [a, b], "vault_commit": vault.commit}
    for sym in ("SPY", "QQQ"):
        s = load(vault, store, sym, FIRST_DATA, OOS_TO)
        cell = simulate(s, "B", "next", COSTS["careful"], start=a, end=b)
        block = {"decision_cell": describe(cell), **({"decision": decide(cell)} if sym == "SPY" else {"robustness_only": decide(cell)}),
                 "halves": [describe(cell.iloc[:len(cell) // 2]), describe(cell.iloc[len(cell) // 2:])],
                 "by_year": {y: describe(g) for y, g in cell.assign(y=lambda d: d.session.str[:4]).groupby("y")},
                 "not_deciding": {"paper_costs": describe(simulate(s, "B", "next", COSTS["paper"], start=a, end=b)),
                                  "decision_price_fills": describe(simulate(s, "B", "decision", COSTS["careful"], start=a, end=b)),
                                  "rule_A": describe(simulate(s, "A", "next", COSTS["careful"], start=a, end=b)),
                                  "vol_target_size": describe(simulate(s, "B", "next", COSTS["careful"], sized="vol", start=a, end=b))}}
        res[sym] = block
        cell.to_csv(out / f"oos_{sym}_daily.csv", index=False)
    (out / "result_oos.json").write_text(json.dumps(res, indent=1) + "\n")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("replicate", "score"))
    ap.add_argument("--bars", type=Path, default=Path("data/alpaca"))
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    from sgt.intraday.store import ParquetBarStore
    from sgt.research.vault import Vault

    vault, store = Vault(STUDY), ParquetBarStore(a.bars)
    rep_path, oos_path = a.out / "result_replication.json", a.out / "result_oos.json"
    if a.cmd == "replicate":
        if rep_path.is_file():
            print(f"{rep_path} exists: the replication run was made once and is not repeated")
            return 2
        res = replicate(vault, store, a.out)
        print(json.dumps({k: res[k] for k in ("gate", "paper_setting")}, indent=1))
        return 0
    if oos_path.is_file():
        print(f"{oos_path} exists: the out-of-sample run was made once and is not repeated")
        return 2
    if not rep_path.is_file() or not json.loads(rep_path.read_text()).get("gate", {}).get("pass"):
        print("the out-of-sample period is opened only after the replication gate has passed")
        return 2
    res = score(vault, store, a.out)
    print(json.dumps({s: {k: res[s][k] for k in res[s] if k in ("decision", "robustness_only", "decision_cell")} for s in ("SPY", "QQQ")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
