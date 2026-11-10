"""Digest layout (owner 2026-11-09 "format the digest better"): dollars first, NEEDS YOU before everything else, one
line per book, GSM-7 only, and long digests sent as numbered parts instead of being cut at SMS_MAX."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sms_control as sc  # noqa: E402
from sgt.fast import fade  # noqa: E402
from sgt.ops import approvals as ap  # noqa: E402
from sgt.ops import digest  # noqa: E402
from sgt.ops.sms import SMS_MAX  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 20, 50, tzinfo=ET)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return OpsStore(tmp_path / "ops.db")


def fade_row(db, sym, session, status, net=None, weight=0.05, news_own=None, alert_type="volume_spike", ib=None, i=[0]):
    i[0] += 1
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, weight,"
                  " net, settled_at, created_at, news_own, ib_avail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sym, session, alert_type, f"{session}T15:00:00+00:00", "-1", i[0], status, weight, net,
                   f"{session}T21:{i[0] % 60:02d}:00+00:00" if status == "settled" else None,
                   f"{session}T15:00:00+00:00", news_own, ib))


def nav_row(db, strategy, session, nav, ret, holdings):
    with db.tx() as c:
        c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                  (strategy, session, nav, ret, json.dumps(holdings)))


def test_holdings_and_dollars():
    assert digest.holdings({"META": -.25, "MSFT": -.25, "NVDA": -.2}) == "short META+MSFT 25%, NVDA 20%"
    assert digest.holdings({"SPY": .6, "IEF": .4, "TLT": -.1}) == "long SPY 60%, IEF 40%; short TLT 10%"
    assert digest.holdings({"SPY": 0.0}) == digest.holdings({}) == "flat"
    assert (digest.usd(-1230.6), digest.usd(906.2), digest.usd(-0.2)) == ("-$1,231", "+$906", "+$0")
    assert digest.name("etf:F1_dual_momentum") == "dual momentum" and digest.name("etf:New_Book") == "New_Book"


def test_news_split_keeps_unknown_trades_apart(db):
    # regression 2026-11-09: news_own NULL grouped as False and overwrote the real no-news group (14 trades, 4 + 4)
    for k, (news, net) in enumerate([(1, -.1), (0, .2), (0, .1), (None, .05), (None, .03)]):
        fade_row(db, f"S{k}", "2026-11-06", "settled", net, news_own=news)
    fade_row(db, "NWS", "2026-11-06", "settled", -.3, news_own=None, alert_type="news")
    line = fade.record_line(db)
    assert "news 2 avg -20.00%, no-news 2 avg +15.00%, news unknown 2 avg +4.00%" in line and "6 trades" in line
    assert "news 2 avg -20.00%, no-news 2 avg +15.00%, news unknown 2 avg +4.00%" in fade.digest_lines(db, DAY)[0]


def test_today_lines_dollars_and_header_total(db):
    fade_row(db, "OLD", "2026-11-06", "settled", 0.10)                  # +0.5% of the book before today
    for sym, net in (("CKX", 0.1389), ("FISN", -0.1574)):
        fade_row(db, sym, "2026-11-09", "settled", net)
    fade_row(db, "OMH", "2026-11-09", "void")
    fade_row(db, "LATE", "2026-11-09", "est_closed")
    nav_row(db, "megacap", "2026-11-06", 100_000, 0.0, {"META": -.25})
    nav_row(db, "megacap", "2026-11-09", 101_000, 0.01, {"META": -.25, "MSFT": -.25})
    nav_row(db, "etf:B_6040", "2026-11-09", 100_500, 0.005, {"SPY": .6, "IEF": .4})
    nav_row(db, "etf:F2_6040_trend", "2026-11-06", 99_000, -0.01, {"SPY": .6, "SHY": .4})
    text = digest.build(db, NOW, fills=[], problems=[], broker=None, research=[])
    before, after = fade.nav(db, DAY - timedelta(days=1)), fade.nav(db, DAY)
    fade_usd = after - before
    assert fade_usd == pytest.approx(before * ((1 + .05 * .1389) * (1 - .05 * .1574) - 1))
    mega = 101_000 - 101_000 / 1.01
    etf = 100_500 - 100_500 / 1.005
    head = text.splitlines()[0]
    assert head.startswith(f"[SGT] Mon 11-09: {digest.usd(fade_usd + mega + etf)} today (paper): ")
    assert f"models {digest.usd(mega)}" in head and f"fade {digest.usd(fade_usd)}" in head
    lines = text.splitlines()
    i = lines.index("TODAY")
    assert lines[i + 1] == f"fade {digest.usd(fade_usd)}, 2 trades: CKX +13.9%, FISN -15.7%, OMH void (1 not settled yet)"
    assert lines[i + 2].startswith(f"megacap {digest.usd(mega)} (+1.00%), month +1.0%, $101,000: short META+MSFT 25%")
    assert lines[i + 3].startswith(f"60/40 {digest.usd(etf)} (+0.50%)")
    assert lines[i + 4].startswith("60/40 trend no close yet (last 11-06)")     # stale book: no dollars invented


def test_needs_you_leads_and_lists_everything(db, monkeypatch, tmp_path):
    log = tmp_path / "x.log"
    log.write_text(f"=== {NOW - timedelta(minutes=5):%Y-%m-%d %H:%M:%S} job\n!!! exit 2\n")
    monkeypatch.setattr(digest, "LOGS", {"megacap": log})
    ap.request(db, lambda t: None, strategy="tom", kind="book", period="p", summary="tom",
               payload={}, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    db.set_flag("paused", "1")
    for k in range(40):                                                    # 40 live F6 losers trip the fade drift alarm
        fade_row(db, f"L{k}", "2026-11-10", "settled", -0.25)
    text = digest.build(db, NOW, fills=[], problems=["x: missing close"], broker=None, research=[])
    needs = text.split("\nTODAY")[0]
    assert "PAUSED" in needs and "- pending tom: OK " in needs and "- jobs FAILED: megacap " in needs
    assert "- x: missing close" in needs and "DRIFT" in needs and "alert fade (no-news)" in needs
    assert "Nothing needs you" not in text


def test_quiet_night_says_nothing_needs_you(db):
    text = digest.build(db, NOW, fills=[], problems=[], broker=None, research=[])
    assert text.splitlines()[1] == "Nothing needs you. Jobs OK (24h)."


def test_fills_show_the_last_position_per_symbol(db):
    fills = [dict(strategy="megacap", symbol="META", target=0.0, price=641.37),
             dict(strategy="megacap", symbol="META", target=-0.25, price=641.37),
             dict(strategy="megacap", symbol="GOOGL", target=0.0, price=288.15)]
    text = digest.build(db, NOW, fills=fills, problems=[], broker=None, research=[])
    assert "Filled at close: megacap: META short 25% @641.37, GOOGL flat @288.15" in text
    assert text.count("META") == 1


def test_output_is_gsm7_only(db, monkeypatch):
    monkeypatch.setattr(fade, "digest_warnings", lambda *a: ["! listener DOWN · since 10:00 → check \U0001F50E"])
    nav_row(db, "etf:B_6040", "2026-11-09", 100_500, 0.005, {"SPY": .6})
    text = digest.build(db, NOW, fills=[], problems=["− 1% ± 2"], broker="café €", research=["x …"])
    assert "listener DOWN ; since 10:00 -> check ?" in text and "- 1% +/- 2" in text and "x ..." in text
    gsm = set(digest._GSM)
    assert all(ch in gsm for ch in text)


def test_parts_never_drop_anything():
    assert digest.parts("short") == ["short"]
    lines = [f"line {i} " + "x" * 90 for i in range(40)]                 # ~4,000 chars
    out = digest.parts("\n".join(lines))
    assert len(out) == 3 and all(len(p) <= SMS_MAX for p in out)
    assert [p.split(" ", 1)[0] for p in out] == ["(1/3)", "(2/3)", "(3/3)"]
    assert "\n".join(p.split(" ", 1)[1] for p in out) == "\n".join(lines)
    long = "y" * 3200                                                       # one over-long line is wrapped, not cut
    out = digest.parts("head\n" + long)
    assert "".join(p.split(" ", 1)[1] for p in out).replace("\n", "") == "head" + long
    assert all(len(p) <= SMS_MAX for p in out)


def test_cmd_digest_sends_every_part(db, monkeypatch):
    sent = []

    class FakeSms:
        def send(self, text):
            sent.append(text)

        def close(self):
            pass
    monkeypatch.setattr(sc, "Sms", FakeSms)
    monkeypatch.setattr(sc.book, "settle", lambda *a, **k: ([], []))
    monkeypatch.setattr(sc, "broker_line", lambda: "broker")
    big = "\n".join(f"line {i} " + "z" * 100 for i in range(30))
    monkeypatch.setattr(sc.digest, "build", lambda *a, **k: big)
    assert sc.cmd_digest(db) == 0
    assert len(sent) == 3 and sent[0].startswith("(1/3)") and "line 29" in sent[-1]
