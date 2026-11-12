# Pre-registration: the trader's five long rules on the alert long book (owner's GEVO conversation, 2026-11-12)

Status: FROZEN before any run. Script: `scripts/trader_rules_long_study.py`, run ONCE. Owner request 2026-11-12: "take
the strategy generally and see if you can optimize our strategy for longs, ONLY if it will actually help".

## Source (the trader, 2026-11-11, on the GEVO long $1.92 -> $1.96 target)
Owner: "This was most optimal for a LONG since the upward spiked right before the sell right?" Trader: "Depends if MACD
was positive or negative." Then: "My core strategy is this: 1. 9ema must cross 24ema 2. Volume must show a spike
3. MACD must be positive 4. PSAR must be green 5. Price must be above VWAP".

## What is already known (not re-tested)
The same family was tested on the gapper universe: every 9/24 cross ~0 before costs (learning_2026-10-17); the trader's
full entry (cross + prior gap + volume spike + PVT + MACD + RSI) +0.34% gross at 30 minutes, CI includes 0, net -0.46%
(ema_cross_check_2026-11-05); PLAYBOOK finding 10. The live long book (first long setup to fill per alert) loses
-2.29% per trade on training (prereg_long_runner_2026-11-11); only 15% of its trades reach the 2R target.
Untested, and tested here: the five rules as a FILTER on the live long book, the five rules as an ENTRY on the alert
stocks, and "hold past the target while MACD is positive" as an EXIT.

## Population (identical to prereg_long_runner_2026-11-11; no new choices)
Alert events (`reports/fade_news` dev + holdout, start bar from `reports/telegram/trades_pooled_first.csv`) and
halt-reopen events (`reports/fade_halt_audit/halt_events.csv`); 1-minute RTH bars `data/alpaca`; the event's round-trip
`cost_rt` (no locate: longs); last entry 15:45 ET (`long_setups_study.latest`). BASE = the live book's trade per event
(`long_runner_study.first_fill`: L4a/L4b/bull flag v2/L6, L7a/L7b after a halt; first to fill; `patterns.simulate` with
the fill-bar target fix; stop, 2R target, 30 minutes). Training = sessions before 2026-07-01, held-out = from 2026-07-01.

## The five conditions (fixed; definitions from the existing code, the trader's 24 EMA)
Indicators per session on the RTH 1-minute bars, causal (pandas `ewm(adjust=False)`, cumulative sums), read at the
CLOSE of a bar k. A missing value (NaN) fails the condition.
- C1 trend: EMA 9 > EMA 24 of the close.
- C2 volume spike: max over bars k-4..k of volume / mean(volume of the 10 bars before that bar, at least 5) >= 5
  (`decision/trader_rules.py` defaults `vol_spike=5.0`, `spike_lookback=5`).
- C3 MACD positive: MACD(12, 26) minus its 9-signal (the histogram, the pane the owner's chart colours) > 0
  (`trader_rules.require_macd_pos`, `signals._indicators`).
- C4 PSAR green: standard parabolic SAR (0.02 step, 0.20 cap) in an up-trend at bar k (`signals._psar_side` on bars
  up to k).
- C5 above VWAP: close > session VWAP (cumulative (high+low+close)/3 x volume / cumulative volume from 09:30).

## Candidates (three; Bonferroni alpha = 0.05 / 3 on training for each)
- **F5 filter** (the rules as a gate on our setups): a BASE trade is KEPT when C1-C5 all hold at the close of the bar
  before its fill bar (the chart a trader sees when the order fills); otherwise skipped. Statistic: mean net of kept
  minus mean net of skipped trades (does the gate pick better trades?). Skipping trades of a losing book raises the
  total by construction, so the total is NOT the statistic.
- **T5 entry** (his strategy on our alert stocks): per event, the first bar k whose close starts at or after the
  event's start bar and before start + 30 minutes (`patterns.WATCH`) where EMA 9 crosses above EMA 24 at k
  (EMA9[k] > EMA24[k] and EMA9[k-1] <= EMA24[k-1]) and C2-C5 hold at k. Buy at the open of the next bar (market,
  `Setup(trigger=None)`), stop = low of bars k-2..k (`trader_rules.trade`, his structural stop), exits as the live book
  (stop first, 2R target, 30 minutes; `patterns.simulate`). One trade per event. Statistic: mean net of T5 trades
  minus mean net of BASE trades (sessions resampled jointly).
- **XM exit** (his GEVO answer): on BASE trades that reach the 2R target, if the histogram is > 0 at the close of the
  bar before the target bar, the whole position is held: each bar from the target bar on, the setup stop is checked
  first (on the target bar itself a low at or below the stop sells at the stop, conservative; later bars at
  min(stop, open)), then a close with histogram <= 0 sells at the next bar's open (that bar's close if it is the
  session's last), else the session's last close. Histogram <= 0 before the target bar: sell at the target (BASE).
  Statistic: paired per-trade XM - BASE (non-target trades are identical).

## Decision (fixed now)
Session-clustered bootstrap (10,000 resamples, seed 20261112), one-sided p = share of resampled statistics <= 0.
- **ADOPT** a candidate for the paper long book only if training statistic > 0 with p < 0.0167, the training minimum is
  met (F5: 100 kept trades; T5: 100 trades; XM: 100 target trades), AND the held-out statistic > 0 (held-out opened only
  for a training pass; held-out minimum 30 kept / T5 / target trades, else under-powered, no adoption). Adoption still
  needs the owner's OK and a prereg amendment.
- **PROFITABLE** (a separate label; needed before any real money): the adopted variant's training mean net > 0 with the
  bootstrap lower 95% bound > 0, AND held-out mean net > 0. An adopted filter that leaves the book losing is reported as
  "better, still losing": it reduces paper losses, it is not an edge.
- Anything else: FAIL, recorded in PLAYBOOK section 4.
Secondary (reported, never decide): each condition alone as a gate (kept - skipped); T5 without a stop marked at 30
minutes (comparable to ema_cross_check); the share of target trades XM holds and why they ended.

## Not done
No parameter search: 9/24, 5x volume over 10 bars within 5 bars, 12/26/9, 0.02/0.20 and the k-2..k stop are the
trader's words or the existing encodings. The three candidates are the only three tested. Held-out is opened once per
candidate, only for a training pass. Note: the held-out window was already used for the base long setups
(prereg_long_setups_2026-11-10); it is unused for these three rules.

## Amendment A1 (2026-11-12, before any result)
The first launch crashed on the first target trade, before writing any output (`cond.hist` resolved to the pandas
plot method, not the histogram column). Fixed to `cond["hist"]`, with an end-to-end per-event test and a stats-path
test added. No rule, parameter or threshold changed; no result was seen. The single run follows this commit.

## Result (run once, 2026-11-12; `reports/trader_rules_long/result.json`) - FAIL (all three)
Training (sessions before 2026-07-01): the live book's construction gives 2,242 trades, BASE -2.29% per trade (as in
prereg_long_runner). Bonferroni alpha 0.0167.
| candidate | training result | verdict |
|---|---|---|
| **F5** gate: all five rules true before the fill | kept 546 trades **-3.13%** vs skipped 1,696 **-2.02%** (diff -1.11%, p 0.997) | FAIL: the rules pick WORSE trades |
| **T5** his entry (fresh 9/24 cross + the other four, within 30 min of the alert) | **15 trades** +0.60% (win 47%; lower95 -2.64%); vs BASE +2.89%, p 0.075; without a stop -0.67% | FAIL: under-powered (100 needed), p above alpha |
| **XM** hold past the 2R target while the MACD histogram is positive | -2.50% vs -2.29% (diff -0.21%, p 0.96); on the 337 target trades +7.41% vs +8.78% | FAIL |
Held-out stays closed (no training pass). Nothing adopted.
Secondary (training, never decides): every rule alone also picks worse trades (kept vs skipped): 9 EMA above 24
-2.36% vs -1.00%; volume spike -3.05% vs -1.97%; MACD positive -2.40% vs -1.29%; PSAR green -2.34% vs -2.01%;
above VWAP -2.33% vs -2.09%. XM on the 337 target trades: 52 sold at the target (MACD not positive), 195 held and sold
when the histogram turned, 89 held into the setup stop, 1 to the close.
Reading: on channel alerts the trader's bullish conditions mark an extended move; buying when all five line up buys
later and higher, so the stop is hit more. His entry almost never happens here (the 9/24 cross is usually already
behind the alert: 15 training trades vs the book's 2,242), and holding a 2R winner for MACD to turn gives back more than the continuation
adds (same answer as the runner exit). Skipping the "all five" trades is not a fix either: the skipped trades still
lose -2.02%. The long book loses before any filter or exit can help.
