# Pre-registration: the daily model as a timing overlay on holding (2026-11-06)

Committed BEFORE any overlay number is computed.

## Why
The daily per-stock model beats holding on none of 21 names as a long/short strategy (Model Tape, PLAYBOOK 6b): it
gives up the market's drift on flat and short days. A gentler use: keep holding the stock and only step aside on the
days the model calls SHORT. If its short calls carry information, the overlay keeps most of the drift and cuts the
worst days.

## Rule (fixed)
Per stock and session: hold the stock (earn the next close-to-close return) unless the model's position is -1; on
those days be in cash (earn 0) and pay one round trip (0.04%: sell at the 15:50-16:00 close, buy back next close).
Compared with simply holding (no cost).

## Decision (forward only; the 2018-2026 out-of-sample record was already examined for the model)
Nightly shadow (`megacapoverlay` job) on the runner's live marks (`reports/megacap_forward/marks.csv`, the six names);
scored on the learning scoreboard. Decided at >= 300 symbol-days (about 10 weeks): RECOMMEND only if overlay minus hold
per session has a session-bootstrap one-sided 95% lower bound > 0 (seed 20261106). Real money needs the owner's OK.

## Secondary result (2026-11-06, 2018-2026 out of sample, already-seen data; not decisive)
Overlay Sharpe above holding on 14 of 21 names. Original six (the model's development names): all six improve (NVDA
1.11 -> 1.23, max drawdown -66% -> -53%; META 0.55 -> 0.71, -77% -> -63%; AMZN drawdown -56% -> -47%). Unseen twelve:
7 better, 5 worse (AVGO, NFLX, ORCL, LLY, XOM). TSLA and AAOI worse. Steps aside on 6-14% of days. Reading: helps
most on the names the model was built on, mixed elsewhere; the forward test decides.

## Secondary (reported now, not decisive)
The same overlay on the 2018-2026 out-of-sample record of all 21 names: return, Sharpe and max drawdown vs holding.
