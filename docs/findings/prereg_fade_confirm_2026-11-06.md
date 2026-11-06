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

## Result (2026-11-06, dev run once, trial ledger `fade-confirm-2026-11-06-dev`) — both FAIL
Champion reproduced `alert_fade_test.score` exactly (guard).

| | training per trade | held-out per trade (secondary) | training diff/session [lower 97.5%] |
|---|---|---|---|
| champion (short at the alert, 60 min) | +0.97% | +1.12% | - |
| E-CONFIRM (wait for breakdown + failed bounce; traded 93%) | -0.08% | +0.24% | -6.92% [-10.24%] |
| X-FAILPB (cover when a pullback fails; 25% covered early) | +0.87% | +0.07% | -0.72% [-2.51%] |

X-FAILPB early covers: -7.57% vs -7.14% if held (training); -7.75% vs -2.96% (held-out).
Reading: the fade's drop starts right at the alert; waiting for a confirmed breakdown sells after most of it. Covering
after a failed pullback locks in losses that usually fade again within the hour (RAYA itself ran to $2.12 at 09:51 and
was back at $1.53 by 10:00). Both rules look right on one chart and lose over 2,349 trades. Nothing deployed.

## What RAYA actually teaches (review 2026-11-06)
1. Execution bug, fixed: the alert came during a LULD halt (no bars 09:38-09:41); the book "entered" at a stale
   pre-halt print ($1.425) instead of the reopen ($1.52). Stale IEX prints are now ignored (fade_live STALE_QUOTE).
2. The trade was in the zero-edge group: news-type alerts or company news earned +0.00%/trade on training (n 1,107)
   and -0.37% held-out (n 277), while no-news alerts earned +1.84% / +1.97% and all of the profit. Our confidence
   number (55) already said this; the book still traded it at full size. That is the mistake not to repeat (owner
   decision pending: skip or shrink news trades).

## Addendum B (2026-11-06, written before computing): the breakeven exit from the owner's review
Owner (2026-11-06): the core of the review is exit timing: "best visible chance to exit was when the pullback brought
it back close to your entry (roughly breakeven)". X-BE (fixed): champion entry; once a 1-minute close is at least 5%
ABOVE the entry (the short is underwater), cover at the next bar's open the first time a close is back at or below
entry x 1.01 (about breakeven). Otherwise the champion's exit. Same gate as above at alpha 0.05 (one new rule), same
data, run once; held-out secondary. Secondary: share covered early and early-cover net vs if held.

## Addendum B result (2026-11-06, run once, trial ledger `fade-breakeven-2026-11-06-dev`) — FAIL
Champion guard passed. X-BE: training +0.38%/trade vs champion +0.97% (diff/session -3.93%, lower 95% -5.72%);
held-out (secondary) +0.01% vs +1.12%. It exits 29% of trades early; those earned +0.28% at breakeven vs +2.30% if
held (training) and +0.36% vs +4.21% (held-out). Reading: a fade that goes 5% against us and then comes back to the
entry usually keeps falling; the breakeven exit gives away the best trades. With the trail, VWAP, EMA, target,
learned and failed-pullback exits this is the eighth exit that loses to the 60-minute hold.

## Addendum C (2026-11-06, written before computing): partial profit + protect the rest (SERV / STAK review)
Owner's review of SERV and STAK (both were in profit, +2.8% and +8%, then exited at the 60-minute mark at about -2.4%
and -2.3%): "take some profit on the first meaningful dip, then protect the remainder as it starts recovering".
X-SCALE (fixed): champion entry. The first time a bar's low reaches entry x 0.97 (+3% for the short), cover HALF at
min(entry x 0.97, that bar's open). From the next bar on, the other half keeps the 60-minute hold but its stop moves to
the entry (breakeven): a bar whose high reaches the entry covers it at max(entry, that bar's open). The 20% stop applies
to both halves until the partial. Gross = the average of the two halves. One rule, alpha 0.05, run once on training
with the champion guard; held-out secondary. Secondary: X-SCALE5 (same with the partial at +5%).

## Addendum C result (2026-11-06, run once each, trial ledger `fade-scale3/5-2026-11-06-dev`) — FAIL
| | training per trade | held-out (secondary) | trades reaching the partial | those trades: scaled vs held |
|---|---|---|---|---|
| champion | +0.97% | +1.12% | - | - |
| X-SCALE (+3%, decides) | -0.50% | -0.67% | 84% / 85% | +2.09% vs +3.84% (train) |
| X-SCALE5 (+5%, secondary) | -0.16% | -0.48% | 75% / 74% | +4.05% vs +5.57% (train) |
Reading: 84% of fades show at least +3% profit at some point in the hour (75% show +5%), so almost every losing trade
has a "best visible exit" in hindsight. Banking half on the first dip and protecting the rest at breakeven caps the
winners while the trades that never dip keep their full 20% stop: the strategy turns from +0.97% to -0.50% per trade.
Ninth post-entry rule that loses to the 60-minute hold. Nothing deployed.
