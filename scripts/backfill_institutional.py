"""Backfill point-in-time institutional-flow data (FINRA short volume, SEC 13F, CUSIP map).

Every step is idempotent and resumable: raw files are cached, per-unit outputs are only
(re)built when missing, consolidated outputs are rewritten atomically. Data only: nothing
here reads prices or computes returns (the daily-extend step only appends bars).

Usage (PYTHONPATH=src:scripts):
    python scripts/backfill_institutional.py finra --start 2016-01-04 --end 2026-10-30
    python scripts/backfill_institutional.py sec13f --start 2015-09-30 --end 2026-10-30
    python scripts/backfill_institutional.py cusip
    python scripts/backfill_institutional.py daily-extend --start 2026-10-19 --end 2026-10-30
    python scripts/backfill_institutional.py status
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from sgt.core.secrets import get_secret
from sgt.institutional import (FetchError, RateLimiter, download_to_file, get_with_retry,
                               sessions, sha256_file, write_parquet_atomic)
from sgt.institutional import cusip_map, finra, sec13f

log = logging.getLogger("backfill_institutional")
FINRA_DIR = Path("data/finra")
SEC_DIR = Path("data/sec13f")
DAILY_DIR = Path("data/daily")
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_SPLIT_LO, _SPLIT_HI = 0.4, 2.5
_OVERLAP_TOL = 1e-6
_DAILY_BATCH = 100


def _setup_logging(logfile: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# -- FINRA ------------------------------------------------------------------------------

def cmd_finra(args: argparse.Namespace) -> int:
    parts, raw = FINRA_DIR / "parts", FINRA_DIR / "raw"
    parts.mkdir(parents=True, exist_ok=True)
    todo = [d for d in sessions(args.start, args.end) if not (parts / f"{d:%Y%m%d}.parquet").exists()]
    log.info("finra: %d sessions to fetch in %s..%s", len(todo), args.start, args.end)
    errors = 0
    limiter = RateLimiter(args.rps)
    limits = httpx.Limits(max_connections=args.workers, max_keepalive_connections=args.workers)
    with httpx.Client(timeout=_TIMEOUT, limits=limits) as client, \
            ThreadPoolExecutor(max_workers=args.workers) as pool, \
            (FINRA_DIR / "day_stats.jsonl").open("a") as stats_fh:
        futs = {pool.submit(finra.load_day, client, d, raw, limiter): d for d in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            d = futs[fut]
            try:
                res = fut.result()
            except (FetchError, finra.FinraFormatError, httpx.HTTPError, OSError) as exc:
                errors += 1
                log.error("finra %s failed: %s", d, exc)
                stats_fh.write(json.dumps({"date": d.isoformat(), "source": "error",
                                           "error": str(exc)[:200]}) + "\n")
                continue
            st = res.stats
            stats_fh.write(json.dumps({"date": d.isoformat(), "source": res.source,
                                       "symbols": len(res.frame), "rows": st.rows,
                                       "rejected": st.rejected, "reasons": st.reasons,
                                       "trailer_mismatch": st.trailer_mismatch}) + "\n")
            stats_fh.flush()
            if res.source == "missing":
                log.warning("finra %s: no CNMS or facility file", d)
            else:
                out = finra.finalize_day(res)
                write_parquet_atomic(pa.Table.from_pandas(out, preserve_index=False),
                                     parts / f"{d:%Y%m%d}.parquet")
            if st.rejected or st.trailer_mismatch:
                log.warning("finra %s: %d rejected %s trailer_mismatch=%s", d, st.rejected,
                            st.reasons, st.trailer_mismatch)
            if n % 100 == 0:
                log.info("finra: %d/%d done", n, len(todo))
    files = sorted(parts.glob("*.parquet"))
    table = pa.concat_tables([pq.read_table(f) for f in files], promote_options="default").sort_by([("date", "ascending"),
                                                                       ("symbol", "ascending")])
    write_parquet_atomic(table, FINRA_DIR / "short_volume.parquet")
    log.info("finra: wrote short_volume.parquet (%d rows, %d dates); %d errors",
             table.num_rows, len(files), errors)
    return 1 if errors else 0


# -- SEC 13F ----------------------------------------------------------------------------

def _sec_client() -> httpx.Client:
    # SEC fair-access policy requires a contact User-Agent; never log or persist it.
    return httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=False,
                        headers={"User-Agent": get_secret("sec_contact")})


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def cmd_sec13f(args: argparse.Namespace) -> int:
    raw, parsed = SEC_DIR / "raw", SEC_DIR / "parsed"
    raw.mkdir(parents=True, exist_ok=True)
    parsed.mkdir(parents=True, exist_ok=True)
    man_path = SEC_DIR / "manifest.json"
    manifest = _load_manifest(man_path)
    limiter = RateLimiter(args.rps)
    last_deadline = args.end + timedelta(days=sec13f.DEADLINE_DAYS + 7)
    with _sec_client() as client:
        page = get_with_retry(client, sec13f.LISTING_URL, max_bytes=sec13f.MAX_LISTING_BYTES,
                              limiter=limiter)
        refs = [r for r in sec13f.parse_listing(page.content)
                if r.window_end >= args.start and r.window_start <= last_deadline]
        log.info("sec13f: %d data sets in scope", len(refs))
        for ref in refs:
            dest = raw / ref.name
            if dest.exists() and ref.name in manifest:
                continue
            if not dest.exists():
                log.info("sec13f: downloading %s", ref.name)
                digest = download_to_file(client, ref.url, dest, max_bytes=sec13f.MAX_ZIP_BYTES,
                                          limiter=limiter)
            else:
                digest = sha256_file(dest)
            manifest[ref.name] = {"url": ref.url, "sha256": digest, "bytes": dest.stat().st_size,
                                  "window": [ref.window_start.isoformat(), ref.window_end.isoformat()],
                                  "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            tmp = man_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
            tmp.replace(man_path)
    for ref in refs:
        stem = ref.name.removesuffix(".zip")
        if (parsed / f"{stem}.holdings.parquet").exists() and not args.reparse:
            continue
        log.info("sec13f: parsing %s", ref.name)
        filings, holdings, stats = sec13f.parse_dataset(raw / ref.name)
        log.info("sec13f: %s %s", ref.name, json.dumps(stats.counts, sort_keys=True))
        (parsed / f"{stem}.stats.json").write_text(json.dumps(stats.counts, indent=1, sort_keys=True))
        write_parquet_atomic(pa.Table.from_pandas(filings, preserve_index=False),
                             parsed / f"{stem}.filings.parquet")
        write_parquet_atomic(pa.Table.from_pandas(holdings, preserve_index=False),
                             parsed / f"{stem}.holdings.parquet")
    windows = [(r.window_start, r.window_end) for r in refs]
    today = datetime.now(timezone.utc).date()
    periods = [p for p in sec13f.quarter_ends(args.start, args.end)
               if sec13f.filing_deadline(p) < today and sec13f.covered(p, windows)]
    skipped = [p for p in sec13f.quarter_ends(args.start, args.end) if p not in periods]
    if skipped:
        log.warning("sec13f: periods without passed deadline or full filing-window coverage: %s",
                    [p.isoformat() for p in skipped])
    agg, summary = sec13f.build_holdings_agg(parsed, periods)
    write_parquet_atomic(pa.Table.from_pandas(agg, preserve_index=False), SEC_DIR / "holdings_agg.parquet")
    write_parquet_atomic(pa.Table.from_pandas(summary, preserve_index=False), SEC_DIR / "periods.parquet")
    log.info("sec13f: holdings_agg %d rows, %d periods", len(agg), len(periods))
    return 0


# -- CUSIP map --------------------------------------------------------------------------

def cmd_cusip(args: argparse.Namespace) -> int:
    agg = pd.read_parquet(SEC_DIR / "holdings_agg.parquet", columns=["cusip", "n_holders"])
    out = SEC_DIR / "cusip_ticker.parquet"
    existing = pd.read_parquet(out) if out.exists() else None
    done = set(existing.loc[existing["status"].isin(cusip_map.DONE_STATUSES), "cusip"]) \
        if existing is not None else set()
    todo = [c for c in cusip_map.eligible_cusips(agg, args.min_holders) if c not in done]
    log.info("cusip: %d eligible not yet mapped", len(todo))
    state = {"df": existing, "pending": [], "batches": 0}

    def checkpoint(recs: list[dict]) -> None:
        state["pending"].extend(recs)
        state["batches"] += 1
        if state["batches"] % 20 == 0:
            state["df"] = cusip_map.merge_results(state["df"], state["pending"])
            state["pending"] = []
            write_parquet_atomic(pa.Table.from_pandas(state["df"], preserve_index=False), out)
            log.info("cusip: checkpoint %d batches, %d records", state["batches"], len(state["df"]))

    key = get_secret("openfigi_api_key", required=False)
    with httpx.Client(timeout=_TIMEOUT) as client:
        try:
            cusip_map.map_cusips(client, todo, api_key=key, on_batch=checkpoint)
        finally:
            state["df"] = cusip_map.merge_results(state["df"], state["pending"])
            write_parquet_atomic(pa.Table.from_pandas(state["df"], preserve_index=False), out)
    log.info("cusip: %s", state["df"]["status"].value_counts().to_dict())
    return 0


# -- daily-extend -----------------------------------------------------------------------

def refetch_reason(stored: pd.DataFrame, fetched: pd.DataFrame) -> str | None:
    """Why the appended window is inconsistent with stored adjusted history, or None.

    Any split/dividend with an ex-date in the window re-adjusts every earlier bar under
    adjustment=all, so overlapping closes that differ mean the stored history is stale.
    Without an overlap bar, fall back to the overnight ratio vs the last stored close.
    """
    if stored.empty or fetched.empty:
        return None
    ov = stored.merge(fetched, on="date", suffixes=("_s", "_f"))
    if len(ov):
        rel = (ov["close_f"] / ov["close_s"] - 1).abs().max()
        return f"overlap_close_diff={rel:.2e}" if rel > _OVERLAP_TOL else None
    new = fetched[fetched["date"] > stored["date"].iloc[-1]]
    if new.empty:
        return None
    ratio = new["open"].iloc[0] / stored["close"].iloc[-1]
    return None if _SPLIT_LO <= ratio <= _SPLIT_HI else f"overnight_ratio={ratio:.3f}"


def refetch_rejected(stored: pd.DataFrame, fresh: pd.DataFrame, start: date) -> str | None:
    """Why a full re-download must not replace the stored file (it would lose history)."""
    if fresh.empty or fresh["date"].iloc[-1] < start.isoformat():
        return "no data in extension window"
    first_old = date.fromisoformat(stored["date"].iloc[0])
    if date.fromisoformat(fresh["date"].iloc[0]) > first_old + timedelta(days=7):
        return f"history starts {fresh['date'].iloc[0]} vs stored {first_old}"
    return None


def append_window(stored: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    new = fetched[fetched["date"] > stored["date"].iloc[-1]]
    out = pd.concat([stored, new.astype(stored.dtypes.to_dict())], ignore_index=True)
    if out["date"].duplicated().any() or not out["date"].is_monotonic_increasing:
        raise ValueError("daily-extend produced duplicate or unsorted dates")
    return out


def cmd_daily_extend(args: argparse.Namespace) -> int:
    from backfill_daily import _rows_to_frame, fetch_batch

    files = {p.stem: p for p in sorted(DAILY_DIR.glob("*.parquet"))}
    syms = [s for s, p in files.items() if pq.read_metadata(p).num_rows > 0]
    lookback = args.start - timedelta(days=7)
    archive = Path("data/daily_replaced") / datetime.now(timezone.utc).strftime("%Y%m%d")
    counts = {"appended": 0, "no_new": 0, "refetched": 0, "refetch_rejected": 0}
    refetch: dict[str, str] = {}
    with httpx.Client(timeout=_TIMEOUT) as client:
        for i in range(0, len(syms), _DAILY_BATCH):
            batch = syms[i:i + _DAILY_BATCH]
            raw = fetch_batch(client, batch, lookback, args.end)
            for sym in batch:
                old, got = pd.read_parquet(files[sym]), _rows_to_frame(raw.get(sym, []))
                if got.empty or not (got["date"] > old["date"].iloc[-1]).any():
                    counts["no_new"] += 1
                    continue
                reason = refetch_reason(old, got)
                if reason:
                    refetch[sym] = reason
                    continue
                if not args.dry_run:
                    write_parquet_atomic(pa.Table.from_pandas(append_window(old, got), preserve_index=False),
                                         files[sym])
                counts["appended"] += 1
            log.info("daily-extend: %d/%d symbols %s", min(i + _DAILY_BATCH, len(syms)), len(syms), counts)
        log.info("daily-extend: %d symbols need full refetch: %s", len(refetch),
                 dict(list(refetch.items())[:20]))
        if not args.dry_run:
            archive.mkdir(parents=True, exist_ok=True)
            items = sorted(refetch)
            for i in range(0, len(items), 20):
                batch = items[i:i + 20]
                raw = fetch_batch(client, batch, args.history_start, args.end)
                for sym in batch:
                    df, old = _rows_to_frame(raw.get(sym, [])), pd.read_parquet(files[sym])
                    reason = refetch_rejected(old, df, args.start)
                    if reason:
                        counts["refetch_rejected"] += 1
                        log.error("daily-extend: refetch of %s rejected (%s); stored file left as is", sym, reason)
                        continue
                    dtypes = old.dtypes.to_dict()
                    shutil.copy2(files[sym], archive / f"{sym}.parquet")
                    df = df.astype(dtypes)
                    write_parquet_atomic(pa.Table.from_pandas(df, preserve_index=False), files[sym])
                    counts["refetched"] += 1
    log.info("daily-extend: done %s (dry_run=%s)", counts, args.dry_run)
    return 0


# -- status -----------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    pd.set_option("display.width", 200)
    sv_path = FINRA_DIR / "short_volume.parquet"
    if sv_path.exists():
        sv = pd.read_parquet(sv_path, columns=["date", "symbol", "source", "total_vol"])
        per_date = sv.groupby("date")["symbol"].size()
        exp = sessions(per_date.index.min().date(), per_date.index.max().date())
        have = set(per_date.index.date)
        missing = [d.isoformat() for d in exp if d not in have]
        print(f"FINRA: {sv['date'].min().date()}..{sv['date'].max().date()} rows={len(sv)} "
              f"symbols={sv['symbol'].nunique()} dates={len(per_date)} missing={len(missing)} {missing[:20]}")
        print("symbols/date:", per_date.describe()[["min", "50%", "max"]].to_dict())
        src = sv.drop_duplicates("date").assign(year=lambda d: d["date"].dt.year)
        print(src.groupby(["year", "source"]).size().unstack(fill_value=0).to_string())
        stats = pd.read_json(FINRA_DIR / "day_stats.jsonl", lines=True).drop_duplicates("date", keep="last")
        print(f"rows parsed={int(stats['rows'].sum())} rejected={int(stats['rejected'].sum())} "
              f"trailer_mismatch_days={int(stats['trailer_mismatch'].fillna(False).sum())} "
              f"errors={int((stats['source'] == 'error').sum())}")
    agg_path = SEC_DIR / "holdings_agg.parquet"
    if agg_path.exists():
        agg = pd.read_parquet(agg_path)
        print(f"\n13F: periods {agg['period'].min().date()}..{agg['period'].max().date()} rows={len(agg)} "
              f"cusips={agg['cusip'].nunique()}")
        print(pd.read_parquet(SEC_DIR / "periods.parquet").to_string(index=False))
        mp = SEC_DIR / "cusip_ticker.parquet"
        if mp.exists():
            m = pd.read_parquet(mp)
            elig = set(cusip_map.eligible_cusips(agg))
            mapped = set(m.loc[m["status"] == "mapped", "cusip"])
            print(f"\nCUSIP map: statuses {m['status'].value_counts().to_dict()} eligible={len(elig)}")
            w = agg[agg["cusip"].isin(elig)]
            print(f"mapped share by count={len(mapped & elig) / max(len(elig), 1):.3f} "
                  f"by holder-weight={w.loc[w['cusip'].isin(mapped), 'n_holders'].sum() / max(w['n_holders'].sum(), 1):.3f}")
    last = pd.Series({p.stem: pq.read_metadata(p).num_rows for p in DAILY_DIR.glob("*.parquet")})
    print(f"\ndaily: files={len(last)} empty={int((last == 0).sum())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finra")
    f.add_argument("--start", type=date.fromisoformat, default=date(2016, 1, 4))
    f.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 30))
    f.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    f.add_argument("--rps", type=float, default=8.0)
    s = sub.add_parser("sec13f")
    s.add_argument("--start", type=date.fromisoformat, default=date(2015, 9, 30), help="first report period")
    s.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 30), help="last report period")
    s.add_argument("--rps", type=float, default=4.0)
    s.add_argument("--reparse", action="store_true", help="rebuild parsed/ from cached zips")
    c = sub.add_parser("cusip")
    c.add_argument("--min-holders", type=int, default=5)
    d = sub.add_parser("daily-extend")
    d.add_argument("--start", type=date.fromisoformat, default=date(2026, 10, 19))
    d.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 30))
    d.add_argument("--history-start", type=date.fromisoformat, default=date(2016, 1, 1))
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    if getattr(args, "rps", 1.0) <= 0 or (args.cmd == "sec13f" and args.rps > 5):
        ap.error("--rps must be > 0 (and <= 5 for SEC)")
    logs = {"finra": FINRA_DIR / "finra.log", "sec13f": SEC_DIR / "sec13f.log",
            "cusip": SEC_DIR / "cusip.log"}
    _setup_logging(logs.get(args.cmd))
    return {"finra": cmd_finra, "sec13f": cmd_sec13f, "cusip": cmd_cusip,
            "daily-extend": cmd_daily_extend, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
