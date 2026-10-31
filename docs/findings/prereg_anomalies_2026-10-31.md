# Pre-registration: three published market anomalies, post-publication data only (2026-10-31)

Committed BEFORE any of these rules is computed on our data. Nothing below may change after scoring.
Context: every signal source tested so far failed (Telegram alerts, MetaTradings stock and option calls,
30-minute and daily per-ticker models incl. 12 unseen names). These are the strongest *published*
calendar/intraday effects that our data can test cheaply. Each is scored on data after its publication
(McLean & Pontiff 2016: published anomalies decay; this is the honest window).

## Family and multiple testing
Three tests, one family. Lower bounds are one-sided at 0.05/3 (bootstrap 10,000, seed 20261031).
The daily per-ticker models used an overnight-gap feature inside an ML model; none of the three rules
below was ever computed in this repo.

## A1 Intraday momentum, SPY (Gao, Han, Li & Zhou 2018, JFE)
- Signal: r1 = SPY price at 10:00 ET (close of the 09:59 1-minute bar) / previous session's 15:59 bar
  close - 1 (1-minute SIP store, raw; dividend ex-dates included, disclosed).
- Trade: at 15:30 ET take position sign(r1) (long or short), entry = open of the 15:30 bar, exit = close
  of the 15:59 bar. Cost 2 bp per round trip. Every regular session; early-close days skipped.
- Window: 2018-07-02 .. 2026-10-29. QQQ reported as secondary.
- PASS if ALL: lower bound of mean daily net > 0; mean > 0 with 2x costs; mean > 0 in both halves.

## A2 Overnight drift, SPY (Cliff, Cooper & Gulen 2008; Lou, Polk & Skouras 2019)
- Trade: long from official close t to official open t+1 (Alpaca adjusted daily bars), flat during the
  day. Cost 2 bp per round trip (MOC buy, MOO sell) every night.
- Window: 2016-01-05 .. 2026-10-29 (start of our adjusted daily data; all post-publication).
- PASS if ALL: lower bound of mean nightly net > 0; mean > 0 with 2x costs; mean > 0 in both halves.
  Reported: net annual return and Sharpe vs buy-and-hold SPY over the same days.

## A3 Turn of the month, SPY (Ariel 1987; Lakonishok & Smidt 1988; McConnell & Xu 2008)
- Trade: long SPY from the close of the second-to-last trading day of each month to the close of the
  third trading day of the next month (4 sessions held), flat otherwise (cash earns 0, conservative).
  Cost 2 bp per round trip. yfinance adjusted closes (`data/etf_yf/adj_close_forward.parquet`).
- Window: windows ending 2000-02 .. 2026-10 (all post-publication).
- PASS if ALL: lower bound of mean net per window > 0; mean > 0 in both halves (split at the median
  window); mean daily return inside windows > mean daily return outside.

## What happens next
A PASS is not deployment. A passing rule gets its own forward paper test on Fly (pre-registered
amendment), like the others. FAIL -> recorded and closed.
