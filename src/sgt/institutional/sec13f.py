"""SEC Form 13F data sets: download, strict parse, point-in-time per-CUSIP aggregation.

Each data-set zip groups filings by FILING date window (``2016q1_form13f.zip`` = filings
made in 2016Q1; from 2024 windows are named ``01dec2025-28feb2026_form13f.zip``), so one
report period's filings can span two zips. Parsing is per zip into ``parsed/`` (filings +
holdings tables); aggregation reads all parsed zips and applies the point-in-time rule.

Rules (2026-11-02, institutional-flow research data layer):
- Only original ``13F-HR`` submissions are kept. ``13F-HR/A`` (restatements and "new
  holdings" amendments) is ignored: amendments arrive at unpredictable times, often after
  the deadline, and a restatement would silently revise history. ``13F-NT`` has no holdings.
- If a CIK files more than one original for a period, the earliest (filing_date,
  accession) wins.
- INFOTABLE rows: ``PUTCALL`` rows dropped; only ``SSHPRNAMTTYPE == 'SH'``; CUSIP upper-
  cased; 6-8 char numeric-prefix CUSIPs are left-zero-padded (spreadsheet damage) only if
  the padded value passes the CUSIP check digit; anything else failing the check digit is
  rejected and counted. Shares are summed per (filing, CUSIP).
- Point-in-time: the legal deadline D(P) is P + 45 days rolled forward to the next
  business day when it falls on a weekend/holiday (Form 13F General Instruction; XNYS
  sessions stand in for SEC business days, identical on every possible P+45 date).
  Period P uses filings with ``P <= FILING_DATE <= D(P)``; ``available_date`` = first
  XNYS session strictly after D(P). A literal ``P + 45`` cut-off was tested first and
  excluded 1,100-2,100 on-time filers (15-25%) in every quarter whose P+45 is a weekend
  (2026-11-02), creating artificial breadth swings.
- ``n_new``/``n_exit`` count holders entering/leaving among CIKs that filed in both P and
  the prior quarter (continuing filers, Chen-Hong-Stein 2002), so a filer that is late or
  stops filing does not register as an exit from every position. ``n_new_raw``/
  ``n_exit_raw`` are the unrestricted set differences. Both are null for the first period.
- VALUE is not stored (units changed from $000s to $ in 2023).
"""
from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.dataset as ds

from sgt.institutional import next_session

log = logging.getLogger(__name__)

LISTING_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
SEC_ROOT = "https://www.sec.gov"
MAX_LISTING_BYTES = 5 * 1024 * 1024
MAX_ZIP_BYTES = 1 << 30
MAX_MEMBER_BYTES = 4 << 30
DEADLINE_DAYS = 45
KEEP_TYPE = "13F-HR"
_HREF = re.compile(r'href="(/files/[a-z]+/data/form-13f-data-sets/'
                   r'([0-9a-z-]{6,40}_form13f\.zip))"')
_OLD_NAME = re.compile(r"^(\d{4})q([1-4])_form13f\.zip$")
_NEW_NAME = re.compile(r"^(\d{2}[a-z]{3}\d{4})-(\d{2}[a-z]{3}\d{4})_form13f\.zip$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_CIK = re.compile(r"^\d{1,10}$")
_CUSIP = re.compile(r"^[0-9A-Z]{9}$")
_QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}


@dataclass(frozen=True)
class DatasetRef:
    name: str
    url: str
    window_start: date
    window_end: date


@dataclass
class ParseStats:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        if n:
            self.counts[key] = self.counts.get(key, 0) + int(n)


def window_of(name: str) -> tuple[date, date]:
    """Filing-date window covered by a data-set zip, from its file name."""
    if m := _OLD_NAME.match(name):
        y, q = int(m.group(1)), int(m.group(2))
        start = date(y, 3 * q - 2, 1)
        end = date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - timedelta(days=1)
        return start, end
    if m := _NEW_NAME.match(name):
        return (datetime.strptime(m.group(1), "%d%b%Y").date(),
                datetime.strptime(m.group(2), "%d%b%Y").date())
    raise ValueError(f"unrecognised 13F data-set name {name!r}")


def parse_listing(html: bytes) -> list[DatasetRef]:
    """Data-set zips linked from the SEC listing page, oldest window first."""
    refs: dict[str, DatasetRef] = {}
    for m in _HREF.finditer(html.decode("utf-8", errors="replace")):
        path, name = m.group(1), m.group(2)
        try:
            start, end = window_of(name)
        except ValueError:
            log.warning("skipping unrecognised data-set link %s", name)
            continue
        refs[name] = DatasetRef(name, SEC_ROOT + path, start, end)
    return sorted(refs.values(), key=lambda r: r.window_start)


def cusip_check_ok(cusip: str) -> bool:
    """Standard CUSIP modulus-10 'double-add-double' check digit."""
    if not _CUSIP.match(cusip) or not cusip[8].isdigit() or cusip == "000000000":
        return False
    total = 0
    for i, ch in enumerate(cusip[:8]):
        v = int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10
        if i % 2:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(cusip[8])


def normalize_cusip(raw: str) -> tuple[str | None, bool]:
    """(valid CUSIP or None, repaired?)."""
    c = raw.strip().upper()
    if cusip_check_ok(c):
        return c, False
    if 6 <= len(c) < 9 and c.isalnum() and c[0].isdigit():
        padded = c.zfill(9)
        if cusip_check_ok(padded):
            return padded, True
    return None, False


def _find_member(zf: zipfile.ZipFile, member: str) -> zipfile.ZipInfo:
    """The single entry named ``member`` at the root or inside one folder (some zips nest)."""
    hits = [i for i in zf.infolist() if not i.is_dir() and i.filename.rsplit("/", 1)[-1] == member]
    if len(hits) != 1:
        raise ValueError(f"expected exactly one {member} in archive, found {len(hits)}")
    return hits[0]


def _read_member(zf: zipfile.ZipFile, member: str, columns: list[str],
                 stats: ParseStats) -> pa.Table:
    info = _find_member(zf, member)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"{member} uncompressed size {info.file_size} exceeds cap")
    bad = [0]

    def on_bad(_row: pacsv.InvalidRow) -> str:
        bad[0] += 1
        return "skip"

    with zf.open(info) as fh:
        tbl = pacsv.read_csv(
            fh,
            parse_options=pacsv.ParseOptions(delimiter="\t", quote_char=False,
                                             newlines_in_values=False, invalid_row_handler=on_bad),
            convert_options=pacsv.ConvertOptions(
                include_columns=columns, column_types={c: pa.string() for c in columns},
                strings_can_be_null=False))
    stats.add(f"{member}:bad_lines", bad[0])
    return tbl


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_filings(sub: pd.DataFrame, cover: pd.DataFrame, stats: ParseStats) -> pd.DataFrame:
    """Validated SUBMISSION rows joined to COVERPAGE report type; originals only."""
    stats.add("submissions", len(sub))
    for t, n in sub["SUBMISSIONTYPE"].value_counts().items():
        stats.add(f"type:{t}", n)
    sub = sub[sub["SUBMISSIONTYPE"] == KEEP_TYPE]
    recs = []
    for acc, fd, cik, per in sub[["ACCESSION_NUMBER", "FILING_DATE", "CIK", "PERIODOFREPORT"]
                                 ].itertuples(index=False):
        fdate, pdate = _parse_date(fd), _parse_date(per)
        if not _ACCESSION.match(acc) or not _CIK.match(cik.strip()) or fdate is None or pdate is None:
            stats.add("filing_rejected:malformed")
            continue
        if (pdate.month, pdate.day) not in _QUARTER_ENDS:
            stats.add("filing_rejected:non_quarter_end_period")
            continue
        if fdate < pdate:
            stats.add("filing_rejected:filed_before_period_end")
            continue
        recs.append((acc, int(cik), pdate, fdate))
    out = pd.DataFrame(recs, columns=["accession", "cik", "period", "filing_date"])
    rtype = cover.drop_duplicates("ACCESSION_NUMBER").set_index("ACCESSION_NUMBER")["REPORTTYPE"]
    out["report_type"] = out["accession"].map(rtype).fillna("")
    stats.add("filings_kept", len(out))
    return out


def parse_infotable(info: pd.DataFrame, keep: set[str], stats: ParseStats) -> pd.DataFrame:
    """Validated share holdings per (accession, cusip) for kept filings."""
    info = info[info["ACCESSION_NUMBER"].isin(keep)]
    stats.add("info_rows", len(info))
    putcall = info["PUTCALL"].str.strip() != ""
    stats.add("info_dropped:putcall", int(putcall.sum()))
    info = info[~putcall]
    not_sh = info["SSHPRNAMTTYPE"].str.strip() != "SH"
    stats.add("info_dropped:not_sh", int(not_sh.sum()))
    info = info[~not_sh]
    shares = pd.to_numeric(info["SSHPRNAMT"].str.strip(), errors="coerce")
    bad_sh = ~(shares > 0) | ~shares.lt(1e13)
    stats.add("info_rejected:bad_shares", int(bad_sh.sum()))
    info, shares = info[~bad_sh], shares[~bad_sh]
    uniq = pd.Series(info["CUSIP"].unique())
    norm = {u: normalize_cusip(u) for u in uniq}
    cus = info["CUSIP"].map(lambda u: norm[u][0])
    stats.add("info_repaired:cusip_zero_pad", int(info["CUSIP"].map(lambda u: norm[u][1]).sum()))
    bad_c = cus.isna()
    stats.add("info_rejected:bad_cusip", int(bad_c.sum()))
    out = pd.DataFrame({"accession": info["ACCESSION_NUMBER"], "cusip": cus,
                        "issuer": info["NAMEOFISSUER"].str.strip().str.slice(0, 200),
                        "shares": shares.astype("float64")})[~bad_c]
    out = (out.groupby(["accession", "cusip"], as_index=False)
              .agg(issuer=("issuer", "first"), shares=("shares", "sum")))
    stats.add("holdings_kept", len(out))
    return out


def parse_dataset(zip_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, ParseStats]:
    """(filings, holdings, stats) for one data-set zip. Holdings carry cik/period/filing_date."""
    stats = ParseStats()
    with zipfile.ZipFile(zip_path) as zf:
        sub = _read_member(zf, "SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE",
                                                   "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"],
                           stats).to_pandas()
        cover = _read_member(zf, "COVERPAGE.tsv", ["ACCESSION_NUMBER", "REPORTTYPE"],
                             stats).to_pandas()
        filings = parse_filings(sub, cover, stats)
        info = _read_member(zf, "INFOTABLE.tsv", ["ACCESSION_NUMBER", "NAMEOFISSUER", "CUSIP",
                                                   "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"],
                            stats).to_pandas()
    holdings = parse_infotable(info, set(filings["accession"]), stats)
    holdings = holdings.merge(filings[["accession", "cik", "period", "filing_date"]], on="accession")
    counts = holdings.groupby("accession").size()
    filings["n_rows"] = filings["accession"].map(counts).fillna(0).astype("int64")
    start, end = window_of(zip_path.name)
    stats.add("filings_outside_window", int((~filings["filing_date"].between(start, end)).sum()))
    return filings, holdings.sort_values(["period", "cusip"]).reset_index(drop=True), stats


def quarter_ends(start: date, end: date) -> list[date]:
    out = []
    for y in range(start.year, end.year + 1):
        for m, d in sorted(_QUARTER_ENDS):
            q = date(y, m, d)
            if start <= q <= end:
                out.append(q)
    return out


def prior_quarter(p: date) -> date:
    return date(p.year, p.month - 2, 1) - timedelta(days=1) if p.month > 3 else date(p.year - 1, 12, 31)


def filing_deadline(p: date) -> date:
    """D(P): first business (XNYS session) day on or after P + 45 days."""
    return next_session(p + timedelta(days=DEADLINE_DAYS - 1))


def covered(p: date, windows: list[tuple[date, date]]) -> bool:
    """True if the union of downloaded filing windows covers ``[P, D(P)]``."""
    need, last = p, filing_deadline(p)
    for s, e in sorted(windows):
        if s <= need <= e:
            need = e + timedelta(days=1)
        if need > last:
            return True
    return False


def select_filings(filings: pd.DataFrame, p: date) -> tuple[pd.DataFrame, int]:
    """Point-in-time originals for period ``p``: one per CIK; returns (kept, n_late)."""
    f = filings[filings["period"] == p]
    deadline = filing_deadline(p)
    late = f["filing_date"] > deadline
    f = f[~late].sort_values(["cik", "filing_date", "accession"]).drop_duplicates("cik")
    return f, int(late.sum())


def aggregate_period(hold: pd.DataFrame, prev: pd.DataFrame | None,
                     filers: set[int], prev_filers: set[int] | None) -> pd.DataFrame:
    """Per-CUSIP breadth for one period from deduped (cik, cusip, issuer, shares) rows."""
    names = (hold.groupby(["cusip", "issuer"]).size().rename("n").reset_index()
             .sort_values(["cusip", "n", "issuer"], ascending=[True, False, True])
             .drop_duplicates("cusip").set_index("cusip")["issuer"])
    agg = hold.groupby("cusip").agg(n_holders=("cik", "nunique"), total_shares=("shares", "sum"))
    agg["issuer"] = names
    if prev is None or prev_filers is None:
        for c in ("n_new", "n_exit", "n_new_raw", "n_exit_raw"):
            agg[c] = pd.array([pd.NA] * len(agg), dtype="Int64")
        return agg.reset_index()
    cur_pairs = hold[["cik", "cusip"]].drop_duplicates()
    prev_pairs = prev[["cik", "cusip"]].drop_duplicates()
    m = cur_pairs.merge(prev_pairs, how="outer", on=["cik", "cusip"], indicator=True)
    cont = m["cik"].isin(filers & prev_filers)
    new = m[m["_merge"] == "left_only"]
    gone = m[m["_merge"] == "right_only"]
    counts = pd.DataFrame({
        "n_new_raw": new.groupby("cusip").size(),
        "n_exit_raw": gone.groupby("cusip").size(),
        "n_new": new[cont[new.index]].groupby("cusip").size(),
        "n_exit": gone[cont[gone.index]].groupby("cusip").size(),
    })
    agg = agg.join(counts, how="outer")
    exited_only = agg["n_holders"].isna()
    if exited_only.any():
        prev_names = prev.drop_duplicates("cusip").set_index("cusip")["issuer"]
        agg.loc[exited_only, "issuer"] = prev_names.reindex(agg.index[exited_only]).to_numpy()
    agg["n_holders"] = agg["n_holders"].fillna(0)
    agg["total_shares"] = agg["total_shares"].fillna(0.0)
    for c in ("n_holders", "n_new", "n_exit", "n_new_raw", "n_exit_raw"):
        agg[c] = agg[c].fillna(0).astype("Int64")
    return agg.rename_axis("cusip").reset_index()


def load_parsed(parsed_dir: Path, p: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All parsed filings and holdings rows (across zips) with ``period == p``."""
    flt = pc.field("period") == pa.scalar(p, pa.date32())
    fl = ds.dataset(sorted(parsed_dir.glob("*.filings.parquet")), format="parquet")
    hd = ds.dataset(sorted(parsed_dir.glob("*.holdings.parquet")), format="parquet")
    return fl.to_table(filter=flt).to_pandas(), hd.to_table(filter=flt).to_pandas()


def build_holdings_agg(parsed_dir: Path, periods: list[date]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(holdings_agg, per-period summary) over consecutive ``periods`` (oldest first)."""
    frames, summary = [], []
    prev: pd.DataFrame | None = None
    prev_filers: set[int] | None = None
    prev_p: date | None = None
    for p in periods:
        filings, hold = load_parsed(parsed_dir, p)
        for c in ("period", "filing_date"):
            filings[c] = pd.to_datetime(filings[c]).dt.date
        kept, n_late = select_filings(filings, p)
        hold = hold[hold["accession"].isin(set(kept["accession"]))]
        filers = set(kept["cik"].astype(int))
        use_prev = prev if prev_p is not None and prev_p == prior_quarter(p) else None
        agg = aggregate_period(hold, use_prev, filers, prev_filers if use_prev is not None else None)
        agg.insert(1, "period", pd.Timestamp(p))
        agg["available_date"] = pd.Timestamp(next_session(filing_deadline(p)))
        frames.append(agg)
        summary.append({"period": pd.Timestamp(p), "n_filers": len(filers),
                        "n_filers_with_rows": int(hold["cik"].nunique()),
                        "n_late_excluded": n_late, "n_dup_originals": int(
                            len(filings[filings["filing_date"] <= filing_deadline(p)])
                            - len(kept)),
                        "n_holding_rows": len(hold), "n_cusips": int((agg["n_holders"] > 0).sum()),
                        "available_date": agg["available_date"].iloc[0] if len(agg) else pd.NaT})
        log.info("period %s: %d filers, %d cusips, %d late excluded", p, len(filers),
                 summary[-1]["n_cusips"], n_late)
        prev, prev_filers, prev_p = hold[["cik", "cusip", "issuer"]], filers, p
    cols = ["cusip", "period", "issuer", "n_holders", "total_shares", "n_new", "n_exit",
            "n_new_raw", "n_exit_raw", "available_date"]
    out = pd.concat(frames, ignore_index=True)[cols] if frames else pd.DataFrame(columns=cols)
    return out, pd.DataFrame(summary)
