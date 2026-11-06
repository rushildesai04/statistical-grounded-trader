# Pre-registration: BUY the channel's later alerts (momentum continuation) (2026-11-06)

Committed BEFORE any long return is computed. Vault study `later-long-2026-11`: training 2025-01-03 .. 2026-06-30
(already seen, as shorts), held-out 2026-07-01 .. 2026-10-29 (later alerts never scored there).

## Why (stated plainly)
Shorting the channel's later alerts lost -3.19%/trade on training (gross -2.09%; prereg_fade_later_alerts): a stock
the channel alerts again an hour after its first alert keeps running. Buying them is a hypothesis FORMED from that
training result, so training is not independent evidence; the decisive test is the sealed held-out period, once.

## Events and trade
Events: exactly the later alerts of prereg_fade_later_alerts (non-first primary alerts of a symbol-day, each >= 60 min
after the previous kept entry). Trade: BUY at the same entry (first 1-minute bar at or after the alert + 10 s, the
shared scorer's entry); stop 20% BELOW the entry (a bar low <= 0.80 x entry sells at min(0.80 x entry, that bar's
open), checked first in each bar); otherwise sell at the close of the last RTH bar within 60 minutes. Cost: the
measured gapper round trip at entry (no locate on a long).

## Gates
Training: reported only. Held-out (decisive, run once via `Vault("later-long-2026-11").unlock`): PASS needs >= 50
trades, mean net > 0 with a day-cluster bootstrap one-sided 95% lower bound > 0 (10,000, seed 20261106), both halves
positive (split 2026-09-01), no single month > 50% of the total. PASS -> paper forward as a long book alongside the
fade (shadow first, forward n >= 100 with a lower bound > 0 before real money; owner OK). FAIL -> written up.

## Secondary
2nd vs 3rd+ alert; by entry window; news / no news; hold 30 / 120 minutes.
Trial ledger run ids `later-long-2026-11-06-dev`, `later-long-2026-11-06-holdout`.
