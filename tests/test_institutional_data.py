"""Parsers and point-in-time rules for the institutional-flow data layer (no network)."""
from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backfill_institutional as bi  # noqa: E402
from sgt.institutional import next_session, sessions  # noqa: E402
from sgt.institutional import cusip_map, finra, sec13f  # noqa: E402

HDR = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"


def finra_file(rows: list[str], trailer: int | None = None, eol: str = "\r\n") -> bytes:
    lines = [HDR, *rows] + ([str(trailer)] if trailer is not None else [])
    return (eol.join(lines) + eol).encode()


# -- FINRA ------------------------------------------------------------------------------

def test_finra_parse_good_file_crlf_and_fractional():
    raw = finra_file(["20261030|A|574482.346219|0|1331315.241444|B,Q,N",
                      "20261030|BRK.B|10|1|20|Q", "20261030|ABRpA|5|0|5|N"], trailer=3)
    df, st = finra.parse_short_file(raw, date(2026, 10, 30))
    assert list(df.symbol) == ["A", "BRK.B", "ABRpA"]
    assert df.loc[0, "short_vol"] == pytest.approx(574482.346219)
    assert st.rejected == 0 and not st.trailer_mismatch


def test_finra_malformed_rows_are_counted_not_fatal():
    raw = finra_file([
        "20160104|AA|10|0|20|Q",
        "20160104|BB|x|0|20|Q",            # bad number
        "20160104|CC|-1|0|20|Q",           # negative
        "20160104|DD|30|0|20|Q",           # short > total
        "20160104|EE|10|15|20|Q",          # exempt > short
        "20160105|FF|1|0|2|Q",             # wrong date
        "20160104|G G;rm|1|0|2|Q",         # bad symbol
        "20160104|HH|1|0|2",               # field count
        "20160104|AA|1|0|2|Q",             # duplicate
    ], trailer=99)
    df, st = finra.parse_short_file(raw, date(2016, 1, 4))
    assert list(df.symbol) == ["AA"] and df.short_vol.iloc[0] == 10
    assert st.rows == 9 and st.rejected == 8 and st.trailer_mismatch
    assert st.reasons == {"bad_number": 2, "short_gt_total": 2, "date_mismatch": 1,
                          "bad_symbol": 1, "field_count": 1, "duplicate_symbol": 1}


@pytest.mark.parametrize("raw", [b"", b"<?xml version='1.0'?><Error/>", "Date|Sym\n".encode(),
                                 "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n\xe9"
                                 .encode("latin-1")])
def test_finra_whole_file_rejected(raw):
    with pytest.raises(finra.FinraFormatError):
        finra.parse_short_file(raw, date(2016, 1, 4))


def test_finra_empty_facility_file_is_valid():
    df, st = finra.parse_short_file(finra_file([], trailer=0), date(2019, 1, 2))
    assert df.empty and st.rows == 0 and not st.trailer_mismatch


def test_finra_facility_sum():
    a, _ = finra.parse_short_file(finra_file(["20160104|X|1|0|10|Q", "20160104|Y|2|1|4|Q"]),
                                  date(2016, 1, 4))
    b, _ = finra.parse_short_file(finra_file(["20160104|X|3|0|5|N"]), date(2016, 1, 4))
    s = finra.combine_facilities([a, b]).set_index("symbol")
    assert s.loc["X"].tolist() == [4, 0, 15] and s.loc["Y"].tolist() == [2, 1, 4]


def test_finra_load_day_prefers_cnms_then_facilities(tmp_path, monkeypatch):
    d = date(2016, 1, 4)
    served = {"FNSQ": finra_file(["20160104|X|1|0|10|Q"], 1), "FNYX": finra_file(["20160104|X|2|0|3|N"], 1)}
    monkeypatch.setattr(finra, "fetch_raw", lambda c, p, day, r, lim=None: served.get(p))
    res = finra.load_day(None, d, tmp_path)
    assert res.source == "FNSQ+FNYX" and res.frame.total_vol.tolist() == [13]
    served["CNMS"] = finra_file(["20160104|X|3|0|13|Q,N"], 1)
    assert finra.load_day(None, d, tmp_path).source == "CNMS"
    served.clear()
    assert finra.load_day(None, d, tmp_path).source == "missing"


def test_finra_available_date_is_next_session():
    out = finra.finalize_day(finra.DayResult(date(2025, 7, 3), "CNMS",
                                             pd.DataFrame({"symbol": ["X"], "short_vol": [1.0],
                                                           "short_exempt_vol": [0.0], "total_vol": [2.0]}),
                                             finra.ParseStats()))
    assert out.available_date.iloc[0] == pd.Timestamp("2025-07-07")  # Jul 4 holiday + weekend
    assert next_session(date(2026, 10, 30)) == date(2026, 11, 2)
    assert sessions(date(2025, 12, 24), date(2025, 12, 26)) == [date(2025, 12, 24), date(2025, 12, 26)]


# -- SEC 13F ----------------------------------------------------------------------------

def test_sec_window_names_and_listing():
    assert sec13f.window_of("2016q1_form13f.zip") == (date(2016, 1, 1), date(2016, 3, 31))
    assert sec13f.window_of("2023q4_form13f.zip") == (date(2023, 10, 1), date(2023, 12, 31))
    assert sec13f.window_of("01dec2025-28feb2026_form13f.zip") == (date(2025, 12, 1), date(2026, 2, 28))
    with pytest.raises(ValueError):
        sec13f.window_of("../../etc_form13f.zip")
    html = (b'<a href="/files/structureddata/data/form-13f-data-sets/2016q1_form13f.zip">x</a>'
            b'<a href="/files/datastandardsinnovation/data/form-13f-data-sets/01jun2026-31aug2026_form13f.zip">'
            b'<a href="https://evil.example/files/x/data/form-13f-data-sets/2017q1_form13f.zip">')
    refs = sec13f.parse_listing(html)
    assert [r.name for r in refs] == ["2016q1_form13f.zip", "01jun2026-31aug2026_form13f.zip"]
    assert all(r.url.startswith("https://www.sec.gov/files/") for r in refs)


def test_cusip_check_digit_and_zero_pad_repair():
    assert sec13f.cusip_check_ok("037833100")      # Apple
    assert sec13f.cusip_check_ok("02079K305")      # Alphabet C
    assert not sec13f.cusip_check_ok("037833101")
    assert sec13f.normalize_cusip(" 37833100 ") == ("037833100", True)
    assert sec13f.normalize_cusip("02079k305") == ("02079K305", False)
    assert sec13f.normalize_cusip("0") == (None, False)
    assert sec13f.normalize_cusip("000000000") == (None, False)          # passes checksum, placeholder
    assert sec13f.normalize_cusip("12345678X") == (None, False)


def _tsv(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join("\t".join(r) for r in [header, *rows]) + "\n"


SUB_H = ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"]
COV_H = ["ACCESSION_NUMBER", "REPORTTYPE"]
INFO_H = ["ACCESSION_NUMBER", "INFOTABLE_SK", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "FIGI", "VALUE",
          "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL", "INVESTMENTDISCRETION", "OTHERMANAGER",
          "VOTING_AUTH_SOLE", "VOTING_AUTH_SHARED", "VOTING_AUTH_NONE"]


def info_row(acc: str, cusip: str, shares: str, typ: str = "SH", putcall: str = "",
             name: str = "APPLE INC") -> list[str]:
    return [acc, "1", name, "COM", cusip, "", "1", shares, typ, putcall, "SOLE", "", "0", "0", "0"]


def make_zip(path: Path, subs: list[list[str]], infos: list[list[str]], extra_line: str = "",
             folder: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{folder}SUBMISSION.tsv", _tsv(SUB_H, subs))
        zf.writestr(f"{folder}COVERPAGE.tsv", _tsv(COV_H, [[s[0], "13F HOLDINGS REPORT"] for s in subs]))
        zf.writestr(f"{folder}INFOTABLE.tsv", _tsv(INFO_H, infos) + extra_line)
    return path


def test_sec_zip_member_nested_folder_and_missing(tmp_path):
    row = [[A1, "10-FEB-2016", "13F-HR", "11", "31-DEC-2015"]]
    z = make_zip(tmp_path / "2016q1_form13f.zip", row, [info_row(A1, "037833100", "1")],
                 folder="01JAN2016_form13f/")
    assert sec13f.parse_dataset(z)[1].shares.tolist() == [1.0]
    with zipfile.ZipFile(tmp_path / "2016q2_form13f.zip", "w") as zf:
        zf.writestr("COVERPAGE.tsv", "x")
    with pytest.raises(ValueError):
        sec13f.parse_dataset(tmp_path / "2016q2_form13f.zip")


A1, A2, A3, A4, A5 = (f"0000000001-16-00000{i}" for i in range(1, 6))


def test_sec_parse_dataset_filters(tmp_path):
    z = make_zip(tmp_path / "2016q1_form13f.zip", [
        [A1, "10-FEB-2016", "13F-HR", "0000000011", "31-DEC-2015"],
        [A2, "11-FEB-2016", "13F-HR/A", "0000000012", "31-DEC-2015"],
        [A3, "12-FEB-2016", "13F-NT", "0000000013", "31-DEC-2015"],
        [A4, "12-FEB-2016", "13F-HR", "0000000014", "15-DEC-2015"],   # non quarter-end
        ["garbage", "xx", "13F-HR", "abc", "31-DEC-2015"],
    ], [
        info_row(A1, "037833100", "100"), info_row(A1, "037833100", "50"),   # summed
        info_row(A1, "037833100", "10", putcall="Call"),
        info_row(A1, "037833100", "10", typ="PRN"),
        info_row(A1, "37833100", "5"),                                        # zero-pad repair
        info_row(A1, "BADCUSIP0", "5"), info_row(A1, "02079K305", "-3"),
        info_row(A2, "037833100", "999"),                                     # amendment: ignored
    ], extra_line="too\tfew\tfields\n")
    filings, hold, st = sec13f.parse_dataset(z)
    assert filings.accession.tolist() == [A1] and filings.n_rows.tolist() == [1]
    assert hold.shares.tolist() == [155.0] and hold.cusip.tolist() == ["037833100"]
    c = st.counts
    assert c["info_dropped:putcall"] == 1 and c["info_dropped:not_sh"] == 1
    assert c["info_rejected:bad_cusip"] == 1 and c["info_rejected:bad_shares"] == 1
    assert c["info_repaired:cusip_zero_pad"] == 1 and c["INFOTABLE.tsv:bad_lines"] == 1
    assert c["filing_rejected:malformed"] == 1 and c["filing_rejected:non_quarter_end_period"] == 1
    assert c["type:13F-HR/A"] == 1 and c.get("filings_outside_window", 0) == 0


def test_sec_period_coverage_and_quarters():
    assert sec13f.prior_quarter(date(2016, 3, 31)) == date(2015, 12, 31)
    assert sec13f.prior_quarter(date(2016, 9, 30)) == date(2016, 6, 30)
    wins = [sec13f.window_of(n) for n in ("2023q4_form13f.zip", "01jan2024-29feb2024_form13f.zip")]
    assert sec13f.covered(date(2023, 12, 31), wins)
    assert not sec13f.covered(date(2024, 3, 31), wins)
    assert sec13f.quarter_ends(date(2015, 9, 30), date(2016, 3, 31)) == [
        date(2015, 9, 30), date(2015, 12, 31), date(2016, 3, 31)]


def _parsed_dir(tmp_path: Path) -> Path:
    q3, q4 = date(2015, 9, 30), date(2015, 12, 31)
    filings = pd.DataFrame([
        ("a1", 1, q3, date(2015, 11, 1)), ("a2", 2, q3, date(2015, 11, 2)),
        ("a3", 3, q3, date(2015, 11, 3)),
        ("b1", 1, q4, date(2016, 2, 1)), ("b2", 2, q4, date(2016, 2, 14)),   # P+45 = Sun 2016-02-14
        ("b2dup", 2, q4, date(2016, 2, 10)),                                  # earlier original wins
        ("b4", 4, q4, date(2016, 2, 1)),
        ("b5late", 5, q4, date(2016, 2, 17)),                                 # after D(P): excluded
        ("b6", 6, q4, date(2016, 2, 16)),                                     # on rolled deadline: kept
    ], columns=["accession", "cik", "period", "filing_date"])
    filings["report_type"], filings["n_rows"] = "13F HOLDINGS REPORT", 1
    hold = pd.DataFrame([
        ("a1", "X"), ("a2", "X"), ("a3", "X"), ("a3", "Z"),
        ("b1", "X"), ("b2", "Y"), ("b2dup", "X"), ("b4", "X"), ("b5late", "X"), ("b6", "W"),
    ], columns=["accession", "cusip"])
    hold["issuer"], hold["shares"] = "ISSUER", 10.0
    hold = hold.merge(filings[["accession", "cik", "period", "filing_date"]], on="accession")
    d = tmp_path / "parsed"
    d.mkdir()
    filings.to_parquet(d / "z.filings.parquet", index=False)
    hold.to_parquet(d / "z.holdings.parquet", index=False)
    return d


def test_sec_point_in_time_aggregation(tmp_path):
    agg, summary = sec13f.build_holdings_agg(_parsed_dir(tmp_path), [date(2015, 9, 30), date(2015, 12, 31)])
    q3 = agg[agg.period == "2015-09-30"].set_index("cusip")
    assert q3.loc["X", "n_holders"] == 3 and pd.isna(q3.loc["X", "n_new"])
    q4 = agg[agg.period == "2015-12-31"].set_index("cusip")
    # filers kept: cik1 (b1), cik2 (b2dup, earliest), cik4, cik6; cik5 late; cik3 stopped filing
    assert q4.loc["X", "n_holders"] == 3 and q4.loc["X", "total_shares"] == 30
    assert q4.loc["X", "n_new_raw"] == 1 and q4.loc["X", "n_new"] == 0     # cik4 not continuing
    assert q4.loc["X", "n_exit_raw"] == 1 and q4.loc["X", "n_exit"] == 0   # cik3 stopped filing
    assert q4.loc["Z", "n_holders"] == 0 and q4.loc["Z", "n_exit_raw"] == 1 and q4.loc["Z", "n_exit"] == 0
    assert "Y" not in q4.index                                               # only in the losing duplicate
    assert q4.loc["W", "n_holders"] == 1 and q4.loc["W", "n_new"] == 0 and q4.loc["W", "n_new_raw"] == 1
    # D(P): Sun 14th -> Mon 15th is Presidents' Day -> Tue 16th; usable from Wed 17th
    assert sec13f.filing_deadline(date(2015, 12, 31)) == date(2016, 2, 16)
    assert (q4.available_date == pd.Timestamp("2016-02-17")).all()
    s = summary.set_index("period").loc[pd.Timestamp("2015-12-31")]
    assert s.n_late_excluded == 1 and s.n_dup_originals == 1 and s.n_filers == 4
    assert sec13f.filing_deadline(date(2026, 6, 30)) == date(2026, 8, 14)   # Friday: no roll


# -- CUSIP map --------------------------------------------------------------------------

def test_openfigi_response_parsing():
    payload = [
        {"data": [{"figi": "BBG000B9XRY4", "name": "APPLE INC", "ticker": "AAPL", "exchCode": "US",
                   "securityType": "Common Stock", "marketSector": "Equity"}]},
        {"warning": "No identifier found."},
        {"error": "Invalid idValue format."},
        {"data": [{"ticker": "X;rm -rf", "marketSector": "Equity"}]},
        "junk",
    ]
    recs = cusip_map.parse_mapping_response(["A", "B", "C", "D", "E"], payload, "now")
    assert [r["status"] for r in recs] == ["mapped", "no_match", "error", "no_match", "invalid"]
    assert recs[0]["ticker"] == "AAPL" and recs[0]["figi"] == "BBG000B9XRY4"
    with pytest.raises(ValueError):
        cusip_map.parse_mapping_response(["A"], [], "now")


def test_eligible_cusips_and_merge():
    agg = pd.DataFrame({"cusip": ["A", "A", "B"], "n_holders": [2, 5, 4]})
    assert cusip_map.eligible_cusips(agg, 5) == ["A"]
    old = cusip_map.merge_results(None, [dict(dict.fromkeys(cusip_map.COLUMNS, ""), cusip="A",
                                              status="error", n_candidates=0)])
    new = cusip_map.merge_results(old, [dict(dict.fromkeys(cusip_map.COLUMNS, ""), cusip="A",
                                             status="mapped", n_candidates=1)])
    assert new.status.tolist() == ["mapped"]


# -- daily-extend -----------------------------------------------------------------------

def bars(dates: list[str], closes: list[float], opens: list[float] | None = None) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "open": opens or closes, "high": closes, "low": closes,
                         "close": closes, "volume": 1.0, "vwap": closes, "trades": 1.0})


def test_daily_extend_detects_adjustment_and_appends_without_duplicates():
    stored = bars(["2026-10-15", "2026-10-16"], [100.0, 101.0])
    same = bars(["2026-10-16", "2026-10-19"], [101.0, 102.0])
    assert bi.refetch_reason(stored, same) is None
    out = bi.append_window(stored, same)
    assert out.date.tolist() == ["2026-10-15", "2026-10-16", "2026-10-19"]
    split = bars(["2026-10-16", "2026-10-19"], [50.5, 51.0])             # 2:1 split re-adjusted overlap
    assert bi.refetch_reason(stored, split).startswith("overlap_close_diff")
    no_overlap = bars(["2026-10-20"], [30.0], opens=[30.0])               # no overlap bar, 3.4x drop
    assert bi.refetch_reason(stored, no_overlap).startswith("overnight_ratio")
    assert bi.refetch_reason(stored, bars(["2026-10-20"], [99.0])) is None


def test_daily_refetch_never_shortens_history():
    stored = bars(["2016-01-04", "2026-10-16"], [10.0, 11.0])
    assert bi.refetch_rejected(stored, bars(["2016-01-04", "2026-10-30"], [9.9, 11.0]), date(2026, 10, 19)) is None
    assert "history starts" in bi.refetch_rejected(stored, bars(["2020-04-20", "2026-10-30"], [5.0, 11.0]),
                                                   date(2026, 10, 19))
    assert bi.refetch_rejected(stored, bars(["2016-01-04"], [9.9]), date(2026, 10, 19)) == "no data in extension window"
