# Pre-registration: risk lessons from the 2026-11-12 losing longs (owner notes on POAS, RETO, ZDGE)

Status: FROZEN before any run. Script: `scripts/long_risk_study.py`, run ONCE. Owner request 2026-11-12: "review the
trades and do a deep dive ... why the failed runs failed with my comments and notes and improve the algorithm in any
way possible only if it makes sense".

## Source (owner notes, Notes tab, 2026-11-11/12)
- POAS long 11-12 (-13.2%): "The stop was 65 cents below entry - 13.2%. That large potential loss was known before
  entering." and "The entry candle closed below $4.92, and the next candle couldn't reclaim it. Buyers weren't
  following through."
- RETO long 11-12 (live -10.7%): "The planned stop allowed roughly 10.7% downside, while the $11.32 target required
  about 21% upside." (the live IEX fill bar opened at 9.33, 11% above the 8.40 buy-stop trigger).
- ZDGE long 11-11 (-2.3%): "the entry candle reversed hard and broke your stop immediately".

## What is already known (not re-tested)
The live long book (first long setup to fill per alert) loses -2.29% per trade on training, n 2,242
(prereg_long_runner_2026-11-11); only 15% of its trades reach the 2R target. Exits that hold longer (runner, MACD hold)
and the trader's five-rule gate all FAIL (prereg_long_runner, prereg_trader_rules_long). Stop width, gap-through fills
and a fill-candle exit were never tested on this book.

## Population (identical to prereg_long_runner_2026-11-11 / prereg_trader_rules_long; no new choices)
Alert events (`reports/fade_news` dev + holdout, start bar from `reports/telegram/trades_pooled_first.csv`) and
halt-reopen events (`reports/fade_halt_audit/halt_events.csv`); 1-minute RTH SIP bars `data/alpaca`; the event's
round-trip `cost_rt`; last entry 15:45 ET. BASE = the live book's trade per event (`long_runner_study.first_fill`:
first long setup to fill; `patterns.simulate`: stop first, 2R target, 30 minutes). Training = sessions before
2026-07-01, held-out = from 2026-07-01.

Per BASE trade: `entry`, `stop`, `trigger` (None for market-entry setups), stop distance `d = (entry - stop) / entry`,
and for buy-stop setups the gap `g = (entry - trigger) / (trigger - stop)` (how many planned R the fill is above the
trigger; 0 when the bar traded through the trigger, > 0 when the fill bar OPENED above it).

## Candidates (four; Bonferroni alpha = 0.05 / 4 = 0.0125 on training for each)
- **W wide-stop skip** (POAS): skip a BASE trade when `d > D80`, D80 = the 80th percentile of `d` over the training
  BASE trades (computed from `d` alone, no outcome). Statistic: mean net of kept minus mean net of skipped trades
  (sessions resampled jointly). Minimum: 100 kept and 100 skipped.
- **R fixed-risk sizing** (POAS, the sizing form of the same lesson): weight `w = min(1, D50 / d)`, D50 = the training
  median of `d`; never larger than today's size. Statistic: return per dollar deployed `sum(w x net) / sum(w)` minus
  the equal-size mean net (paired, sessions resampled). Minimum 100 trades.
- **G gap-through skip** (RETO): skip a buy-stop BASE trade when `g > 1` (the fill opened more than one planned R above
  the trigger, so the real risk is more than double the plan). Market-entry setups are always kept. Statistic: kept
  minus skipped. Minimum: 100 skipped (fewer: under-powered, FAIL).
- **E red fill-candle exit** (POAS, ZDGE): a BASE trade not closed on its fill bar (no stop or target there) whose fill
  bar CLOSES below the entry price is sold at the next bar's open (the next bar of the session; if none, that close).
  All other trades unchanged. Statistic: paired per-trade E - BASE. Minimum: 100 trades changed by the rule.

## Decision (fixed now)
Session-clustered bootstrap (10,000 resamples, seed 20261112), one-sided p = share of resampled statistics <= 0.
- **ADOPT** for the paper long book only if training statistic > 0 with p < 0.0125, the training minimum is met, AND
  the held-out statistic > 0 (held-out opened only for a training pass; held-out minimum 30 skipped / trades / changed
  trades, else under-powered, no adoption). Adoption still needs the owner's OK.
- **PROFITABLE** (separate label; needed before any real money): the adopted variant's training mean net per trade
  (W, G: kept trades; R: per dollar; E: all trades) > 0 with bootstrap lower 95% bound > 0, AND held-out > 0. An
  adopted rule that leaves the book losing is reported as "better, still losing".
- Anything else: FAIL, recorded in PLAYBOOK section 4.
Secondary (reported, never decide): mean net by quintile of `d`; share of trades with `g > 0`; E's changed-trade count
and mean; the same four on held-out only when the candidate passes training.

## Not done
No threshold search: D80, D50, `g > 1` and "close below entry" are fixed here before any outcome is seen. No other
candidates. Note: the held-out window was already used for the base long setups (prereg_long_setups_2026-11-10); it is
unused for these four rules.
