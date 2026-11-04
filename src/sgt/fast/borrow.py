"""Borrow reality for short trades (plan_2026-11-04 W1): can the stock actually be shorted, and at what fee?

Two public sources, no account needed:
- IBKR stock-loan file: documented public FTP ``ftp2.interactivebrokers.com`` (user ``shortstock``, empty
  password), ``usa.txt``: symbol | currency | name | conid | ISIN | rebate rate | fee rate (% per year) |
  shares available (">10000000" style lower bounds) | FIGI. Refreshed by IBKR several times per day.
  Snapshots are kept (``data/borrow/YYYYMMDD/HHMM.parquet``): borrow history becomes research data.
- Alpaca asset flags (``/v2/assets/{symbol}``): ``shortable`` and ``easy_to_borrow`` at alert time.

The FTP payload is external input: size-bounded, parsed strictly, malformed rows dropped and counted.
"""
from __future__ import annotations

import ftplib
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
HOST, USER, FILE = "ftp2.interactivebrokers.com", "shortstock", "usa.txt"
MAX_BYTES = 20_000_000
ROOT = Path("data/borrow")
STALE = timedelta(hours=3)                     # an older snapshot is not used to stamp a trade
_SYM = re.compile(r"^[A-Z]{1,5}([. ][A-Z])?$")


def fetch_ibkr(timeout: float = 60.0) -> bytes:
    buf, size = io.BytesIO(), 0

    def sink(chunk: bytes) -> None:
        nonlocal size
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValueError("IBKR borrow file larger than expected")
        buf.write(chunk)
    with ftplib.FTP(HOST, timeout=timeout) as f:
        f.login(USER, "")
        f.retrbinary(f"RETR {FILE}", sink)
    return buf.getvalue()


def parse_ibkr(payload: bytes) -> tuple[pd.DataFrame, datetime | None]:
    """-> (symbol, fee, rebate, avail) for US common-style symbols, and the file's own timestamp (ET->UTC)."""
    text = payload.decode("latin-1")
    stamp, rows, bad = None, [], 0
    for line in text.splitlines():
        if line.startswith("#BOF|"):
            try:
                _, d, t = line.split("|")[:3]
                stamp = datetime.strptime(f"{d} {t}", "%Y.%m.%d %H:%M:%S")
            except ValueError:
                stamp = None
            continue
        if not line or line.startswith("#"):
            continue
        f = line.split("|")
        if len(f) < 8 or f[1] != "USD" or not _SYM.match(f[0]):
            continue
        try:
            rows.append((f[0].replace(" ", "."), float(f[6]), float(f[5]), int(f[7].lstrip(">") or 0)))
        except ValueError:
            bad += 1
    if bad:
        log.warning("IBKR borrow file: %d malformed rows dropped", bad)
    df = pd.DataFrame(rows, columns=["symbol", "fee", "rebate", "avail"]).drop_duplicates("symbol")
    if stamp is not None:
        from zoneinfo import ZoneInfo
        stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    return df, stamp


def snapshot(now: datetime, root: Path = ROOT, payload: bytes | None = None) -> Path:
    df, stamp = parse_ibkr(payload if payload is not None else fetch_ibkr())
    if len(df) < 1000:
        raise ValueError(f"IBKR borrow file has only {len(df)} US rows; refusing to store")
    at = stamp or now
    d = root / f"{at:%Y%m%d}"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{at:%H%M}.parquet"
    df.assign(snap=at.isoformat()).to_parquet(out)
    df.assign(snap=at.isoformat()).to_parquet(root / "latest.parquet")
    return out


class BorrowBook:
    """Latest IBKR snapshot (reloaded when the file changes) + per-day cached Alpaca flags."""

    def __init__(self, root: Path = ROOT, alpaca_asset=None) -> None:
        self.root, self._mtime, self._df = root, None, None
        self._alpaca = alpaca_asset
        self._flags: dict[tuple[str, str], tuple[int | None, int | None]] = {}

    def _latest(self) -> pd.DataFrame | None:
        p = self.root / "latest.parquet"
        if not p.exists():
            return None
        m = p.stat().st_mtime
        if m != self._mtime:
            self._df, self._mtime = pd.read_parquet(p).set_index("symbol"), m
        return self._df

    def lookup(self, symbol: str, now: datetime) -> dict:
        out: dict = {"ib_avail": None, "ib_fee": None, "ib_snap": None, "alp_etb": None, "alp_shortable": None}
        df = self._latest()
        if df is not None and len(df):
            snap = datetime.fromisoformat(str(df.snap.iloc[0]))
            if now - snap <= STALE:
                out["ib_snap"] = snap.isoformat()
                if symbol in df.index:
                    r = df.loc[symbol]
                    out["ib_avail"], out["ib_fee"] = int(r.avail), float(r.fee)
                else:
                    out["ib_avail"] = 0                 # not in IBKR's lendable list = not shortable there
        if self._alpaca is not None:
            key = (symbol, now.date().isoformat())
            if key not in self._flags:
                try:
                    a = self._alpaca(symbol)
                    self._flags[key] = (int(bool(a.get("easy_to_borrow"))), int(bool(a.get("shortable"))))
                except Exception as exc:  # noqa: BLE001 - flags are informational; logged, left NULL
                    log.warning("alpaca asset %s failed: %s", symbol, type(exc).__name__)
                    self._flags[key] = (None, None)
            out["alp_etb"], out["alp_shortable"] = self._flags[key]
        return out
