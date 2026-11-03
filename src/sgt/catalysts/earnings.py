"""Earnings release times from SEC EDGAR: 8-K filings that carry Item 2.02 (Results of Operations).

Source: ``https://data.sec.gov/submissions/CIK##########.json`` (``filings.recent`` plus the older
pages listed in ``filings.files``). Only form ``8-K`` (not 8-K/A) with ``2.02`` in ``items``.
EDGAR's ``acceptanceDateTime`` is UTC (verified 2026-11-03: AAPL's 16:30 ET releases show 20:30Z in
summer and 21:30Z in winter); it is converted to America/New_York.
The User-Agent is the SEC fair-access contact from the keychain; it is never logged or stored.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUB_URL = "https://data.sec.gov/submissions/{name}"
MAX_BYTES = 50_000_000
MIN_INTERVAL = 0.15                            # < 10 requests/s (SEC fair access)


def parse_acceptance(s: str) -> datetime:
    """'2023-11-02T20:30:28.000Z' (UTC) -> 2023-11-02 16:30:28 America/New_York."""
    s = (s or "").strip()
    if len(s) < 19 or s[10] != "T":
        raise ValueError(f"bad acceptanceDateTime {s[:40]!r}")
    return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc).astimezone(ET)


def earnings_rows(block: dict) -> list[dict]:
    """One submissions block (``filings.recent`` or an older page) -> Item 2.02 8-K rows."""
    forms, items, acc = block.get("form") or [], block.get("items") or [], block.get("acceptanceDateTime") or []
    accn = block.get("accessionNumber") or []
    if not (len(forms) == len(items) == len(acc) == len(accn)):
        raise ValueError("submissions arrays differ in length")
    out = []
    for f, it, a, n in zip(forms, items, acc, accn):
        if f == "8-K" and "2.02" in str(it).split(","):
            out.append({"accepted": parse_acceptance(a), "accession": str(n)[:25]})
    return out


class Edgar:
    def __init__(self, user_agent: str, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False,
                                             headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last = 0.0

    def _get(self, url: str) -> dict:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.client.get(url)
        if r.status_code != 200:
            raise RuntimeError(f"EDGAR {url.rsplit('/', 1)[-1]}: HTTP {r.status_code}")
        if len(r.content) > MAX_BYTES:
            raise RuntimeError(f"EDGAR {url.rsplit('/', 1)[-1]}: response too large")
        j = r.json()
        if not isinstance(j, dict):
            raise RuntimeError("EDGAR: unexpected JSON shape")
        return j

    def ciks(self) -> dict[str, int]:
        return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in self._get(TICKERS_URL).values()}

    def earnings(self, cik: int) -> pd.DataFrame:
        j = self._get(SUB_URL.format(name=f"CIK{cik:010d}.json"))
        f = j.get("filings") or {}
        rows = earnings_rows(f.get("recent") or {})
        for page in f.get("files") or []:
            name = str(page.get("name", ""))
            if not (name.startswith(f"CIK{cik:010d}-submissions-") and name.endswith(".json")):
                raise RuntimeError(f"unexpected submissions page name {name[:60]!r}")
            rows += earnings_rows(self._get(SUB_URL.format(name=name)))
        return pd.DataFrame(rows, columns=["accepted", "accession"]).drop_duplicates("accession")

    def close(self) -> None:
        self.client.close()


# ---------------------------------------------------------------- events -> trades (prereg_earnings_2026-11-03)
OPEN_T, CLOSE_T = 9 * 60 + 30, 16 * 60
DEDUPE_SESSIONS = 20
COST = 0.0010
CONFIGS: dict[str, dict] = {}
for _d, _dn in ((1, "cont"), (-1, "rev")):
    for _m in (0.01, 0.03):
        for _x in (0, 1, 3):
            CONFIGS[f"open_{_dn}_m{_m:.0%}_x{_x}"] = {"entry": "open", "d": _d, "m": _m, "exit": _x}
    for _m in (0.02, 0.05):
        for _x in (1, 5):
            CONFIGS[f"close_{_dn}_m{_m:.0%}_x{_x}"] = {"entry": "close", "d": _d, "m": _m, "exit": _x}


def reaction_session(t: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """Session whose open first reflects a release accepted at ``t`` (ET); None if released in RTH."""
    day = pd.Timestamp(t.date())
    mins = t.hour * 60 + t.minute
    if day in sessions:
        if mins < OPEN_T:
            return day
        if mins < CLOSE_T:
            return None
        i = sessions.searchsorted(day, side="right")
    else:
        i = sessions.searchsorted(day, side="left")
    return sessions[i] if i < len(sessions) else None


def events(raw: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """Raw EDGAR rows (``accepted``) -> kept reaction sessions ``R`` (repeat filings deduped)."""
    r = [reaction_session(t, sessions) for t in raw.accepted.sort_values()]
    kept, last = [], None
    for x in r:
        if x is None:
            continue
        pos = sessions.get_loc(x)
        if last is not None and pos - last <= DEDUPE_SESSIONS:
            continue
        kept.append(x)
        last = pos
    return pd.DataFrame({"R": kept})


def event_prices(ev: pd.DataFrame, px: pd.DataFrame, spy: pd.DataFrame) -> pd.DataFrame:
    """Per event: open_R, close at R-1, R, R+1, R+3, R+5 for the stock and SPY, and the exit dates.
    ``px``/``spy``: adjusted daily bars indexed by Timestamp. Events lacking any bar are dropped."""
    idx = spy.index
    rows = []
    for R in ev.R:
        if R not in idx or R not in px.index:
            continue
        i = idx.get_loc(R)
        if i < 1 or i + 5 >= len(idx):
            continue
        days = {n: idx[i + n] for n in (-1, 0, 1, 3, 5)}
        if not all(d in px.index for d in days.values()):
            continue
        row = {"R": R, "open_R": px.open[R], "s_open_R": spy.open[R]}
        for n, d in days.items():
            k = "_1" if n == -1 else str(n)
            row[f"c{k}"], row[f"s_c{k}"], row[f"d{k}"] = px.close[d], spy.close[d], d
        rows.append(row)
    return pd.DataFrame(rows)


def config_trades(p: pd.DataFrame, cfg: dict, cost: float = COST) -> pd.DataFrame:
    """Event prices -> trades for one configuration: R, exit date, side, net (SPY-hedged), raw."""
    if p.empty:
        return pd.DataFrame(columns=["R", "exit", "side", "net", "raw", "x"])
    if cfg["entry"] == "open":
        x = p.open_R / p.c_1 - 1
        e, se = p.open_R, p.s_open_R
        if cfg["exit"] == 0:
            out, sout, ex = p.c0, p.s_c0, p.d0
        else:
            out, sout, ex = p[f"c{cfg['exit']}"], p[f"s_c{cfg['exit']}"], p[f"d{cfg['exit']}"]
    else:
        x = p.c0 / p.c_1 - 1
        e, se = p.c0, p.s_c0
        out, sout, ex = p[f"c{cfg['exit']}"], p[f"s_c{cfg['exit']}"], p[f"d{cfg['exit']}"]
    side = cfg["d"] * (x > 0).astype(int).replace(0, -1)
    keep = x.abs() >= cfg["m"]
    r, rs = out / e - 1, sout / se - 1
    t = pd.DataFrame({"R": p.R, "exit": ex, "side": side, "x": x,
                      "net": side * (r - rs) - cost, "raw": side * r - cost})
    return t[keep.to_numpy()].reset_index(drop=True)


def date_lower_bound(t: pd.DataFrame, alpha: float, rng, n_boot: int = 10_000, col: str = "net") -> float:
    """One-sided lower bound of the mean per event, resampling reaction dates (clusters)."""
    if t.empty:
        return float("nan")
    g = t.groupby("R")[col].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(float), g["count"].to_numpy(float)
    idx = rng.integers(0, len(g), size=(n_boot, len(g)))
    return float(np.quantile(s[idx].sum(axis=1) / c[idx].sum(axis=1), alpha))
