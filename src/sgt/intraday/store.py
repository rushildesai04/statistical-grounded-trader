"""Immutable Parquet store for 1-minute bars.

Layout (Hive-style, one file per symbol per session)::

    <root>/bars_1m/date=YYYY-MM-DD/symbol=XYZ.parquet

Files are immutable once written: :meth:`ParquetBarStore.write_session` refuses to
overwrite an existing file unless ``overwrite=True`` is passed explicitly, and
writes atomically (temp file + ``os.replace``) so a crash never leaves a partial
file. A read round-trips to byte-for-byte identical :class:`Bar` objects.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from sgt.config import get_settings
from sgt.core.types import Bar, Segment

_UTC = timezone.utc
_SUBDIR = "bars_1m"

# Explicit schema so nullability/precision survive a round-trip exactly.
_SCHEMA = pa.schema(
    [
        pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.float64(), nullable=False),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("session_date", pa.string(), nullable=False),
        pa.field("minute_of_day", pa.int32(), nullable=False),
        pa.field("segment", pa.string(), nullable=False),
    ]
)

_FRAME_COLUMNS = [f.name for f in _SCHEMA]


class ParquetBarStore:
    def __init__(self, root: Path | str | None = None) -> None:
        base = Path(root) if root is not None else Path(get_settings().data_dir)
        self.root = base / _SUBDIR

    # -- paths --------------------------------------------------------------
    def _date_dir(self, session_date: date) -> Path:
        return self.root / f"date={session_date.isoformat()}"

    def _file(self, symbol: str, session_date: date) -> Path:
        return self._date_dir(session_date) / f"symbol={symbol.upper()}.parquet"

    # -- write --------------------------------------------------------------
    def write_session(
        self, symbol: str, session_date: date, bars: list[Bar], *, overwrite: bool = False
    ) -> Path:
        """Write all ``bars`` for one ``symbol``/``session_date`` atomically.

        Validates every bar belongs to this symbol and session and that timestamps
        are strictly increasing. Refuses to overwrite an existing file unless
        ``overwrite=True``.
        """
        symbol = symbol.upper()
        if not bars:
            raise ValueError("write_session requires at least one bar")
        prev_ts: datetime | None = None
        for b in bars:
            if b.symbol != symbol:
                raise ValueError(f"bar symbol {b.symbol!r} != {symbol!r}")
            if b.session_date != session_date:
                raise ValueError(
                    f"bar session_date {b.session_date} != {session_date} for {symbol}"
                )
            if prev_ts is not None and b.ts <= prev_ts:
                raise ValueError(
                    f"bars must be strictly increasing in ts: {prev_ts} then {b.ts}"
                )
            prev_ts = b.ts

        target = self._file(symbol, session_date)
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"{target} already exists; pass overwrite=True to replace (store is immutable)"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        table = pa.table(
            {
                "ts": pa.array([b.ts for b in bars], type=_SCHEMA.field("ts").type),
                "symbol": [b.symbol for b in bars],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
                "vwap": [b.vwap for b in bars],
                "trade_count": [b.trade_count for b in bars],
                "session_date": [b.session_date.isoformat() for b in bars],
                "minute_of_day": pa.array(
                    [b.minute_of_day for b in bars], type=pa.int32()
                ),
                "segment": [b.segment.value for b in bars],
            },
            schema=_SCHEMA,
        )

        tmp = target.with_suffix(f".parquet.tmp.{os.getpid()}")
        try:
            pq.write_table(table, tmp)
            os.replace(tmp, target)  # atomic on POSIX
        finally:
            if tmp.exists():
                tmp.unlink()
        return target

    # -- read ---------------------------------------------------------------
    def _read_table(self, symbol: str, session_date: date) -> pa.Table | None:
        path = self._file(symbol, session_date)
        if not path.exists():
            return None
        return pq.read_table(path, schema=_SCHEMA)

    @staticmethod
    def _rows_to_bars(table: pa.Table) -> list[Bar]:
        cols = table.to_pydict()
        bars: list[Bar] = []
        for i in range(table.num_rows):
            ts = cols["ts"][i]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_UTC)
            else:
                ts = ts.astimezone(_UTC)
            tc = cols["trade_count"][i]
            bars.append(
                Bar(
                    symbol=cols["symbol"][i],
                    ts=ts,
                    open=cols["open"][i],
                    high=cols["high"][i],
                    low=cols["low"][i],
                    close=cols["close"][i],
                    volume=cols["volume"][i],
                    session_date=date.fromisoformat(cols["session_date"][i]),
                    minute_of_day=int(cols["minute_of_day"][i]),
                    segment=Segment(cols["segment"][i]),
                    vwap=cols["vwap"][i],
                    trade_count=int(tc) if tc is not None else None,
                )
            )
        return bars

    def read(self, symbol: str, start: date, end: date) -> list[Bar]:
        """All bars for ``symbol`` across sessions in ``[start, end]`` (inclusive),
        time-ordered."""
        symbol = symbol.upper()
        out: list[Bar] = []
        for d in self.sessions(symbol):
            if start <= d <= end:
                table = self._read_table(symbol, d)
                if table is not None:
                    out.extend(self._rows_to_bars(table))
        out.sort(key=lambda b: b.ts)
        return out

    def read_frame(self, symbol: str, start: date, end: date):
        """Pandas DataFrame of the same bars (research view)."""
        import pandas as pd

        symbol = symbol.upper()
        tables: list[pa.Table] = []
        for d in self.sessions(symbol):
            if start <= d <= end:
                table = self._read_table(symbol, d)
                if table is not None:
                    tables.append(table)
        if not tables:
            return pd.DataFrame(columns=_FRAME_COLUMNS)
        combined = pa.concat_tables(tables)
        df = combined.to_pandas()
        df = df.sort_values("ts").reset_index(drop=True)
        return df[_FRAME_COLUMNS]

    # -- discovery ----------------------------------------------------------
    def sessions(self, symbol: str) -> list[date]:
        """Session dates for which ``symbol`` has a file, ascending."""
        symbol = symbol.upper()
        if not self.root.exists():
            return []
        out: list[date] = []
        for d_dir in self.root.glob("date=*"):
            if not d_dir.is_dir():
                continue
            if (d_dir / f"symbol={symbol}.parquet").exists():
                out.append(date.fromisoformat(d_dir.name[len("date="):]))
        return sorted(out)

    def symbols(self, session_date: date | None = None) -> list[str]:
        """Symbols present, for one ``session_date`` or across all sessions, sorted."""
        if not self.root.exists():
            return []
        if session_date is not None:
            dirs = [self._date_dir(session_date)]
        else:
            dirs = [d for d in self.root.glob("date=*") if d.is_dir()]
        found: set[str] = set()
        for d_dir in dirs:
            if not d_dir.is_dir():
                continue
            for f in d_dir.glob("symbol=*.parquet"):
                found.add(f.name[len("symbol="):-len(".parquet")])
        return sorted(found)
