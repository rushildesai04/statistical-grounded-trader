"""Owner-facing fade texts: the entry card (FADE IN / request), the FADE OUT text and their display-only history
tables (confidence by news group, SEC filings, time of day). Split from fade.py 2026-11-06 (file size); the live
book imports and re-exports everything here, so callers keep using ``fade.ask_text`` etc."""
from __future__ import annotations

from datetime import datetime

from sgt.fast import news
from sgt.fast.fade_rule import (AVG_LOSS, AVG_WIN, BASE_EV, BASE_N, BASE_WIN, ET, JUDGE_N, STOP, STOP_RATE,
                                STOPPED_NET, VETO_EXIT)
from sgt.ops import chartserver
from sgt.ops.sms_style import bold, italic

# Confidence (prereg_fade_news_2026-11-05 A1, reports/fade_news/confidence.json): 100 x P(mean net of trades like
# this one > 0), session bootstrap over all 3,109 tested events in the trade's news group. Display only. The
# alert-time price/volume features carry no per-trade signal (prereg_fade_confidence); company news does.
# group: (confidence, win rate, avg net, n)
CONF = {"none": (100, 0.58, 0.0165, 1839), "own": (55, 0.53, 0.0007, 1004), "earn": (55, 0.52, 0.0014, 266),
        "all": (100, 0.56, 0.0101, 3109)}


CONF_MAX = 99                                   # never show certainty


CONF_LABEL = {"none": "no company news", "own": "company news today", "earn": "earnings news today",
              "all": "news check failed"}


# History by SEC filing at the alert (prereg_fade_filings_2026-11-05, dev 2025-01..2026-06; both gates FAIL, so
# display only): (win rate, avg net, n). Offering in 30 days vs not; 8-K/6-K since yesterday's close vs not.
FILING_HIST = {"dil": (0.57, 0.0108, 511), "no_dil": (0.54, 0.0095, 1481), "8k": (0.52, 0.0079, 401)}


PLAIN = {"volume_increasing": "volume rising", "volume_spike": "volume spike", "gaining_volume": "volume rising",
         "gaining_momentum": "momentum", "new_high": "new high", "intraday_high": "high of day",
         "testing_high": "testing high", "price_level": "price level", "resumed": "halt resumed", "news": "news"}


def _fmt(px: float | None) -> str:
    return "n/a" if px is None else (f"{px:.2f}" if px >= 1 else f"{px:.4f}")


def _qty(n: int) -> str:
    return f"{n / 1e6:.0f}M" if n >= 1e6 else (f"{n / 1e3:.0f}k" if n >= 1e3 else str(n))


def borrow_note(r) -> str:
    """'IBKR 75k sh @ 44%/yr' / 'none at IBKR (real short unlikely)' / '' (unknown); Alpaca easy-to-borrow."""
    r, parts = dict(r), []
    if r.get("ib_avail") is not None:
        fee = "fee n/a" if r.get("ib_fee") is None else f"@ {r['ib_fee']:.0f}%/yr"
        parts.append("none at IBKR (real short unlikely)" if r["ib_avail"] <= 0 else
                     f"IBKR {_qty(r['ib_avail'])} sh {fee}")
    if r.get("alp_etb") == 1:
        parts.append("Alpaca easy")
    return "; ".join(parts)


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


# Confidence v2 (prereg_fade_news amendment A2, 2026-11-06; display only). Owner: "why is it always 55?" - v1 had two
# levels. Same definition (100 x P(mean net of trades like this > 0), session bootstrap over all 3,109 tested fades), finer
# "like this": news (company news found OR a news-type alert) x entry window. cell -> (confidence, win, avg net, n).
CONF2 = {("news", "pre11"): (38, 0.53, -0.0014, 1006), ("news", "11-14"): (47, 0.52, -0.0007, 292),
         ("news", "14-16"): (66, 0.55, 0.0071, 86), ("none", "pre11"): (98, 0.57, 0.0117, 1074),
         ("none", "11-14"): (100, 0.59, 0.0274, 491), ("none", "14-16"): (100, 0.65, 0.0398, 160),
         ("all", "pre11"): (93, 0.55, 0.0054, 2080), ("all", "11-14"): (100, 0.56, 0.0169, 783),
         ("all", "14-16"): (100, 0.61, 0.0283, 246)}
WINDOW_LABEL = {"pre11": "before 11:00 ET", "11-14": "11:00-14:00 ET", "14-16": "after 14:00 ET"}


def window(at: datetime) -> str:
    t = at.astimezone(ET)
    m = t.hour * 60 + t.minute
    return "pre11" if m < 11 * 60 else ("11-14" if m < 14 * 60 else "14-16")


def confidence(alert_type: str, ctx: dict, at: datetime | None) -> tuple[int, float, float, str]:
    """(confidence 0-99, win rate, avg net, label) for the entry text and the stored ``conf``. Without a time, v1."""
    g = news_group(ctx)
    if at is None:
        conf, win, avg, _ = CONF[g]
        return min(conf, CONF_MAX), win, avg, CONF_LABEL[g]
    cell = "news" if alert_type == "news" or g in ("own", "earn") else g
    conf, win, avg, _ = CONF2[(cell, window(at))]
    what = ("news alert" if alert_type == "news" and g not in ("own", "earn") else CONF_LABEL[g]) if cell == "news" \
        else CONF_LABEL[g]
    return min(conf, CONF_MAX), win, avg, f"{what}, {WINDOW_LABEL[window(at)]}"


def news_group(ctx: dict) -> str:
    """'none' / 'own' / 'earn' from the live news flags; 'all' when the news lookup failed (unknown)."""
    n = ctx.get("news")
    if not n:
        return "all"
    return "earn" if n.get("news_earn") else ("own" if n.get("news_own") else "none")


def signals_line(sig: dict | None) -> str | None:
    """Chart read at entry (owner request 2026-11-05): RSI, EMA 9/21, MACD, PSAR, PVT and how many say 'stretched'.
    Display only: these reads were tested and carry no edge on their own (docs/research/signals_research_2026-11-05)."""
    if not sig:
        return None
    parts, votes = [], []
    if sig.get("rsi14") is not None:
        parts.append(f"RSI {sig['rsi14']:.0f}")
        votes.append(sig["rsi14"] >= 70)
    if sig.get("ema_cross_state") is not None:
        gap = sig.get("ema9_21_gap_pct")
        parts.append(f"EMA9 {'above' if sig['ema_cross_state'] > 0 else 'below'} 21"
                     + (f" ({gap:+.1%})" if gap is not None else ""))
        votes.append(sig["ema_cross_state"] < 0)
    if sig.get("macd_hist_pct") is not None:
        parts.append("MACD " + ("fading" if sig["macd_hist_pct"] < 0 else "rising"))
        votes.append(sig["macd_hist_pct"] < 0)
    if sig.get("psar_side") is not None:
        votes.append(sig["psar_side"] < 0)
    if sig.get("pvt_trend") is not None:
        votes.append(sig["pvt_trend"] < 0)
    if sig.get("off_hod_pct") is not None:
        votes.append(sig["off_hod_pct"] >= -0.02)
    if not parts and not votes:
        return None
    tail = f" · {sum(votes)}/{len(votes)} reads favor the short" if votes else ""
    return "📈 " + " · ".join(parts) + tail + " " + italic("(chart read, not a tested filter)")


# Champion net by ET entry window, training 2025-01..2026-06 (prereg_fade_hold_2026-11-05 secondary; display only,
# time-of-day sizing is a forward shadow, prereg_fade_tod_2026-11-06): (win rate, avg net, label).
TOD_HIST = ((11 * 60, (0.55, 0.0058, "Before 11:00 ET")), (14 * 60, (0.55, 0.0163, "11:00-14:00 ET")),
            (24 * 60, (0.59, 0.0233, "After 14:00 ET")))


def tod_hist(at: datetime) -> tuple[float, float, str]:
    t = at.astimezone(ET)
    m = t.hour * 60 + t.minute
    return next(v for cut, v in TOD_HIST if m < cut)


def social_line(st: dict | None) -> str | None:
    """StockTwits attention at the alert (owner decision 2026-11-06). Recorded forward; not a tested filter."""
    if not st or st.get("st_span_min") is None:
        return None
    watch = st.get("st_watch")
    w = f" · {watch / 1e3:.0f}k watching" if watch and watch >= 1000 else (f" · {watch} watching" if watch else "")
    return (f"🗣 StockTwits: {st['st_n60']} posts last hour ({st['st_bull']} bull / {st['st_bear']} bear); latest 30 "
            f"span {st['st_span_min']:.0f} min{w} " + italic("(retail buzz, recorded, not a tested filter)"))


def x_line(xs: dict) -> str:
    """X attention (counts exact; accounts / likes over the newest few posts, the cheap sample, 2026-11-08)."""
    head = f"𝕏 {xs['x_n60']} posts last hour"
    if xs.get("x_authors") is None or not xs.get("x_k"):
        return head
    return f"{head}; newest {xs['x_k']}: {xs['x_authors']} accounts, {xs['x_eng']:,} likes/reposts"


def reddit_line(rd: dict) -> str:
    """Reddit stock-subreddit mentions over 24 h (ApeWisdom, 2026-11-08)."""
    if rd.get("rd_rank") is None:
        return "👽 Reddit: not among the ~700 most-mentioned stock tickers today"
    prev = rd.get("rd_mentions_prev")
    was = f" (was {prev} a day earlier)" if prev is not None else ""
    return f"👽 Reddit: #{rd['rd_rank']} most-mentioned, {rd['rd_mentions']} mentions in 24h{was}"


def ask_text(symbol: str, alert_type: str, px: float | None, size: float, ctx: dict, borrow: str,
             live: tuple[int, float | None] = (0, None), out_at: datetime | None = None,
             at: datetime | None = None) -> str:
    """Trade text body (owner requests 2026-11-04/05): trade in plain words, confidence by news group, headline,
    why, tape, risk, tested payoff, live record, borrow, chart. ``out_at`` set = the F5 entry text (already short).
    Unicode bold + emoji = UCS-2 SMS; see sms_style."""
    head = f"{bold(f'SHORT {symbol}')} @ {bold('$' + _fmt(px)) if px else 'market'}"
    conf, win, avg, label = confidence(alert_type, ctx, at)
    lines = [f"✅ {bold('FADE IN')} · {head}" if out_at else f"🔻 {head}",
             f"💬 {'Shorted' if out_at else 'Short'} ${size:,.0f} of {symbol}: we make money if it falls",
             f"🧠 Confidence {bold(f'{conf}')}/100 · {label}: "
             + italic(f"trades like this win {win:.0%}, avg {avg:+.1%}")]
    head_line = (ctx.get("news") or {}).get("headline")
    if head_line:
        lines.append(f"📰 “{head_line}”")
    fl = ctx.get("filings")
    if fl:                                       # None = EDGAR unknown: say nothing rather than "no filings"
        sec = (f"⚠️ offering filed ({fl['dilution_form']}, {fl['dilution_days']:.0f}d ago): company selling shares"
               if fl.get("dilution_30d") else "no offering filed in 30 days")
        hw, ha, _ = FILING_HIST["8k" if fl.get("eightk_today") else ("dil" if fl.get("dilution_30d") else "no_dil")]
        lines.append(f"📄 SEC: {sec}" + (" · 8-K/6-K today" if fl.get("eightk_today") else "")
                     + " " + italic(f"(past fades like this: win {hw:.0%}, avg {ha:+.1%})"))
    line = social_line(ctx.get("social"))
    if line:
        lines.append(line)
    xs = ctx.get("x")
    if xs:
        lines.append(x_line(xs) + " " + italic("(recorded, not a tested filter)"))
    rd = ctx.get("reddit")
    if rd:
        lines.append(reddit_line(rd) + " " + italic("(recorded, not a tested filter)"))
    why = [PLAIN.get(alert_type, alert_type)]
    if ctx.get("chg_day") is not None:
        why.append(f"{'up' if ctx['chg_day'] >= 0 else 'down'} {abs(ctx['chg_day']):.0%} today")
    if ctx.get("chg_open") is not None:
        why.append(f"{ctx['chg_open']:+.0%} from open")
    lines.append("📣 " + " · ".join(why))
    tape = []
    if ctx.get("off_high") is not None:
        tape.append(f"{abs(ctx['off_high']):.0%} off high")
    if ctx.get("vwap") and px:
        tape.append(f"{px / ctx['vwap'] - 1:+.0%} vs VWAP ${_fmt(ctx['vwap'])}")
    if ctx.get("rvol"):
        tape.append(f"vol {ctx['rvol']:.1f}x yday")
    if tape:
        lines.append("📊 " + " · ".join(tape) + " " + italic("(IEX)"))
    sig_line = signals_line(ctx.get("signals"))
    if sig_line:
        lines.append(sig_line)
    stop = f"${_fmt(px * (1 + STOP))}" if px else "+20%"
    hold = f"⏰ out {out_at.astimezone(ET):%H:%M} ET" if out_at else "⏳ 60 min"
    lines += [f"💵 ${size:,.0f} · 🛑 stop {stop} ≈ {_usd(size * STOPPED_NET)} · {hold}",
              f"🎯 Tested ({BASE_N} unseen): win {BASE_WIN:.0%}, avg {bold(_usd(size * BASE_EV))}",
              f"    wins {_usd(size * AVG_WIN)} · losses {_usd(size * AVG_LOSS)} · {STOP_RATE:.0%} stopped"]
    n, mean = live
    if n and mean is not None:
        lines.append(f"📈 Live: {n} trades, avg {mean:+.1%}" + (" " + italic("(too few to judge)") if n < JUDGE_N else ""))
    none = borrow.startswith("none")
    lines.append(f"🏦 {'⚠️ ' if none else ''}{borrow or 'borrow unknown'}")
    chart = news.chart_url(symbol)
    if chart:
        lines.append(f"🔗 {chart}")
    return "\n".join(lines)


EXIT_WHY = {"stop": "🛑 stop hit: price jumped 20% over our short, loss capped", "60 min": "⏰ 60 minutes up",
            "close": "🔔 market close", VETO_EXIT: "👋 you texted NO"}


def out_text(r, entry: float, exit_px: float, why: str, usd: float, day_usd: float, n_day: int) -> str:
    """FADE OUT text (owner request 2026-11-05: plain words): result first, what happened, why, the day so far."""
    r = dict(r)
    g = (entry - exit_px) / entry
    mark = "🛑" if why == "stop" else ("🟢" if g > 0 else "🔴")
    verb = "made" if usd >= 0 else "lost"
    lines = [f"[SGT] {mark} {bold('FADE OUT')} · {bold(r['symbol'])} · {verb} {bold(f'{_usd(usd)} ({g:+.1%})')}",
             f"Shorted ${_fmt(entry)} → bought back ${_fmt(exit_px)} · {EXIT_WHY.get(why, why)}"]
    move = exit_px / entry - 1
    because = (f"price fell {abs(move):.1%} while we were short" if move < 0 else
               f"price rose {move:.1%} while we were short" if move > 0 else "price did not move")
    if r.get("news_own"):
        because += " · it had company news (" + ("earnings" if r.get("news_earn") else "news") + \
                   "), where fades only break even on average"
    if r.get("filing_dilution"):
        because += f" · an offering was filed ({r.get('filing_form') or 'S-1/424B'}) in the last 30 days"
    lines += [f"💡 Why: {because}", f"📅 Today: {n_day} trades, {bold(_usd(day_usd))}"]
    chart = news.chart_url(r["symbol"])
    if chart:
        lines.append(f"📈 {chart}")
    page = chartserver.link()
    if page:
        lines.append(f"🖼 All trades: {page}")
    lines.append(italic("Estimate before costs; official numbers in the midnight summary."))
    return "\n".join(lines)
