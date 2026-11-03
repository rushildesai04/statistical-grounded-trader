# Pre-registration: earnings-reaction trades on large caps, hours to 5 days (2026-11-03)

Committed BEFORE any return around an earnings event is computed. Event times were fetched first
(EDGAR timestamps only, no prices). Study id `earnings-2026-11` in the holdout vault
(`config/holdout_registry.json`): development 2016-01-04 .. 2023-12-29, held-out 2024-01-02 ..
2026-10-29. The scorer (`scripts/earnings_study.py score`) reads held-out events only after the vault
confirms this file and the scorer are committed unchanged, once (unlock recorded in the trial ledger).

## Why
User choice 2026-11-03 after the fast large-cap models failed (held-out gross 0-5 bp per trade against
a 4 bp cost). Earnings reactions are 5-10x larger moves, so cost is a small fraction of the move; a
post-announcement drift (continuation) and a large-cap overreaction (reversal) are both documented.
Both directions are tested; development data chooses, held-out data decides.

## Universe (fixed now, before any return was computed; current large US caps, no BRK.B)
AAPL MSFT AMZN GOOGL META NVDA TSLA AVGO JPM V MA LLY UNH XOM WMT ORCL AMD NFLX COST HD PG JNJ ABBV
MRK PEP KO CVX BAC WFC C GS MS AXP BLK SCHW ADBE CRM CSCO INTC IBM QCOM TXN AMAT MU INTU NOW AMGN
GILD BMY PFE TMO DHR ABT MDT ISRG CVS CI ELV MCD SBUX NKE LOW TGT BKNG DIS CMCSA T VZ TMUS CAT DE HON
GE MMM BA LMT RTX UPS FDX UNP NEE DUK SO LIN COP SPG AMT PLD PYPL MO PM MDLZ CL KHC GM F USB ACN UBER
(98 names). Survivorship: these are today's large caps; the primary return is hedged with SPY so that
"stocks that became large went up" does not count as edge.

## Events (`sgt.catalysts.earnings`)
- An event is an SEC form 8-K (not 8-K/A) whose items include 2.02 (Results of Operations), with its
  EDGAR acceptance time T (UTC, converted to ET).
- Reaction session R: T before 09:30 ET on a session -> that session; T at/after 16:00 ET, or on a
  non-session day -> the next session. T between 09:30 and 16:00 on a session -> excluded (released
  during trading; the information time is unclear).
- Per name, an event within 20 sessions after a kept event is dropped (repeat filings).
- Needs adjusted daily bars (Alpaca, `adjustment=all`) for R-1 .. R+5; otherwise excluded.
- Coverage known at freeze time (event fetch only, `reports/earnings_fetch.log`, no prices examined):
  98 names, 2-95 Item 2.02 filings each. XOM (2) and BLK (9) rarely or only recently use Item 2.02;
  DIS, CI, LIN, AVGO (30-34) have pre-reorganization filings under an older CIK, which is not fetched.
  These names simply contribute fewer events; nothing is substituted.

## Trades (20 configurations, all declared now)
Prices: open_R, close_R, close_{R+n} from adjusted daily bars; SPY over the same window.
- Open-entry family (12): signal x = gap = open_R / close_{R-1} - 1, known at the open (decision uses
  the official open as a proxy for the 09:29 indication; declared simplification). Enter at open_R,
  side = d x sign(x) with d in {+1 continuation, -1 reversal}, only if |x| >= m with m in {1%, 3%};
  exit at close_R (same day, ~6.5 h), close_{R+1}, or close_{R+3}.
- Close-entry family (8): signal x = close_R / close_{R-1} - 1 (the full reaction day). Enter at
  close_R, side = d x sign(x), only if |x| >= m with m in {2%, 5%}; exit at close_{R+1} or close_{R+5}.
- Primary return per event: side x (stock return - SPY return over the same entry -> exit) - 10 bp
  (round trip on the stock, event-day spread, plus the SPY hedge).

## Development and selection (events with R and exit both in 2016-01-04 .. 2023-12-29)
Every configuration is reported. Statistic: mean net per event and its 95% one-sided lower bound from a
bootstrap that resamples reaction DATES (events on the same date move together), 10,000 resamples,
seed 20261103. Selected: the configuration with the highest lower bound among those with >= 150
development events. If that lower bound is <= 0 the study STOPS: the vault stays locked, verdict FAIL
(development).

## Held-out decision (selected configuration only; events with R in 2024-01-02 .. 2026-10-29 and exit
## on or before 2026-10-29; scored once)
PASS only if ALL hold:
1. Mean net per event > 0 with the date-bootstrap 95% one-sided lower bound > 0;
2. Mean net per event > 0 with costs doubled (20 bp);
3. At least 2 of the 3 calendar years 2024, 2025, 2026 have a positive mean;
4. At least 100 held-out events.
PASS -> forward paper record on the Fly runner (event calendar from EDGAR, virtual book, SMS-approved
like the other books) before any money. FAIL -> earnings-reaction trading on large caps is closed.

## Reported, never used for the decision
All 20 configurations on held-out data (printed after the verdict is written to the ledger), unhedged
returns, events per year, and an indicative annual return at 5% of capital per event.

## Contamination statement
Daily returns of 19 of these names (2016-2026) were used by earlier per-ticker and fast-intraday studies,
never conditioned on earnings dates. No return around an earnings event has been computed for any name
in this repository before this file. Trial ledger run id `earnings-2026-11-03`.
