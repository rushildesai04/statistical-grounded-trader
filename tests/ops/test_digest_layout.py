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
from sgt.ops.sms_style import bold, italic  # noqa: E402
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
    head, split = text.splitlines()[:2]
    assert head == f"[SGT] \U0001F4CA {bold('Mon 11-09')}: {bold(digest.usd(fade_usd + mega + etf))} today {italic('(paper)')}"
    assert f"models {digest.usd(mega)}" in split and f"fade {digest.usd(fade_usd)}" in split
    lines = text.splitlines()
    i = lines.index(f"\U0001F4B5 {bold('TODAY')}")
    assert fade_usd < 0 and digest.dot(fade_usd) == "\U0001F534"
    assert lines[i + 1] == (f"\U0001F534 {bold('fade')} {digest.usd(fade_usd)}, 2 trades: CKX +13.9%, FISN -15.7%, OMH void "
                            f"({italic('1 not settled yet')})")
    assert lines[i + 2].startswith(f"\U0001F7E2 {bold('megacap')} {digest.usd(mega)} (+1.00%), month +1.0%, $101,000: "
                                   "short META+MSFT 25%")
    assert lines[i + 3].startswith(f"\U0001F7E2 {bold('60/40')} {digest.usd(etf)} (+0.50%)")
    assert lines[i + 4].startswith(f"\u23F8\uFE0F {bold('60/40 trend')} {italic('no close yet')} (last 11-06)")  # no dollars invented


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
    needs = text.split(bold("TODAY"))[0]
    assert bold("NEEDS YOU") in needs and "PAUSED" in needs and "\u2022 jobs FAILED: megacap " in needs
    assert "\u2022 x: missing close" in needs and "DRIFT" in needs and "alert fade (no-news)" in needs
    from sgt.fast import fade                                              # 2026-11-12: the alarm halves new fades
    assert "size for new trades cut to 50% (was 100%). Ask Claude to restore it." in needs   # and says so (F-1)
    assert fade.drift_scale(db) == 0.5
    code = ap.pending(db)[0]["code"]
    assert f"\u2022 pending tom: OK {code} by " in needs                    # reply code stays plain ASCII digits
    assert "Nothing needs you" not in text


def test_quiet_night_says_nothing_needs_you(db):
    text = digest.build(db, NOW, fills=[], problems=[], broker=None, research=[])
    assert text.splitlines()[1] == "\u2705 Nothing needs you. Jobs OK (24h)."


def test_fills_show_the_last_position_per_symbol(db):
    fills = [dict(strategy="megacap", symbol="META", target=0.0, price=641.37),
             dict(strategy="megacap", symbol="META", target=-0.25, price=641.37),
             dict(strategy="megacap", symbol="GOOGL", target=0.0, price=288.15)]
    text = digest.build(db, NOW, fills=fills, problems=[], broker=None, research=[])
    assert f"{bold('Filled at close')}: megacap: META short 25% @641.37, GOOGL flat @288.15" in text
    assert text.count("META") == 1


def test_styling_is_safe(db, monkeypatch):
    # owner 2026-11-09: emojis, bold, italics; control characters from any section never reach the phone
    monkeypatch.setattr(fade, "digest_warnings", lambda *a: ["! listener DOWN\x1b[31m since 10:00\x07"])
    nav_row(db, "etf:B_6040", "2026-11-09", 100_500, 0.005, {"SPY": .6})
    text = digest.build(db, NOW, fills=[], problems=["bad\rline"], broker="Alpaca \x00paper", research=["r\x0bx"])
    assert "\u2022 listener DOWN[31m since 10:00" in text and "\u2022 badline" in text and "Alpaca paper" in text
    assert not any(ord(ch) < 32 and ch != "\n" for ch in text) and "\x7f" not in text
    assert bold("60/40") in text and "\U0001F7E2" in text and bold("RECORD") in text


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
    styled = "\n".join(bold(f"book {i} ") + "\U0001F7E2" * 20 + " +$1,000" for i in range(60))   # 2-unit chars
    out = digest.parts(styled)
    assert len(out) > 1 and all(len(p.encode('utf-16-le')) // 2 <= 1500 for p in out)   # carriers count UTF-16
    assert "\n".join(p.split(" ", 1)[1] for p in out) == styled


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


def test_fade_off_line_and_forward_record_problems_reach_the_digest(tmp_path, monkeypatch):
    # owner 2026-11-12 night: shorts off -> the digest says so every night; a broken forward record is a NEEDS YOU line
    from sgt.learning import live_monitor
    monkeypatch.chdir(tmp_path)
    db = OpsStore(tmp_path / "ops" / "ops.db")
    db.set_flag(fade.OFF_FLAG, "1")
    fade_row(db, "OFFX", "2026-11-09", "filtered")
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET reason=? WHERE symbol='OFFX'", (fade.OFF_REASON,))
    p = live_monitor.forward_path(db)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("symbol,session,wrong\nA,2026-11-02,0.1\n")
    text = digest.build(db, NOW, fills=[], problems=[], broker=None, research=[])
    lines = text.splitlines()
    i = lines.index(f"\U0001F4B5 {bold('TODAY')}")
    assert lines[i + 1].startswith(f"⏸️ {bold('fade')} {italic('OFF')} (owner 11-12, shorts deprioritized): "
                                   "1 alerts not shorted today")
    assert f"• fade forward record (every alert): forward record unreadable (KeyError)" in lines
