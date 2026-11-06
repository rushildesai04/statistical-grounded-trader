# Pre-registration: confirmation entry and failed-pullback exit (RAYA review, 2026-11-06)

Committed BEFORE any number in this file is computed.

## Why
RAYA (2026-11-06) was shorted while still rising near its high (RSI ~88-96, company news) and stopped out after a
failed pullback. An outside review of the chart proposed two rules: (1) do not short into strength; wait until the
stock breaks down and a bounce fails below its high; (2) once the trade has pulled back in our favor, cover when price
breaks back above the post-entry high (the pullback failed). Both are judged on history, not on one chart. Related
prior results: entering 2-4 minutes late loses most of the edge (prereg_fade_challengers D2/D4); every exit that
reacts to bounces earned less than the 60-minute hold (prereg_fade_exits, prereg_fade_exit_learn).

## Challengers (fixed; champion = the frozen rule; same events, costs, 20% stop, 60-minute hold, RTH bars)
- E-CONFIRM (entry): from the champion's entry bar, track the running high H. A breakdown is a 1-minute close at least
  3% below H. After a breakdown, the first bar whose high stays below H and that closes red (close < open) is the
  failed bounce; short at the NEXT bar's open, then the champion's 20% stop and 60-minute hold from that entry. No
  failed bounce within 20 minutes of the champion's entry = no trade (net 0 for that event, as in D2/D4).
- X-FAILPB (exit): champion entry; track the post-entry high Hp (entry bar included). Once a close is at least 3%
  below the entry, cover at the next bar's open the first time a close is above Hp. Otherwise the champion's exit.

## Gate (training 2025-01-03 .. 2026-06-30 only; run once) — same as prereg_fade_challengers
Per challenger: paired per-session difference of total net vs the champion, session bootstrap (10,000, seed
20261106), one-sided lower bound at alpha 0.05/2 > 0, and 1st-percentile per-trade net no worse than the champion's
minus 5 points. Champion must reproduce `alert_fade_test.score` exactly (guard). Held-out 2026-07..10 secondary.
PASS -> nightly shadow, decided forward at n >= 300; a live change needs the owner's OK. FAIL -> written up.

## Secondary (reported only)
Share of events traded (E-CONFIRM), mean entry delay, mean per traded event; X-FAILPB share covered early, early-cover
gross vs if held; both on the news / no-news split.
Trial ledger run id `fade-confirm-2026-11-06-dev`.
