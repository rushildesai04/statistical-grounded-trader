# Pre-registration: size the fade by its confidence score — forward only (2026-11-06)

Committed BEFORE any forward number for this rule is computed.

## Why
Owner (2026-11-06): bets must be conscious of the confidence. Confidence v2 (prereg_fade_news A2) is 38-66 for news
alerts / company news and 93-99 otherwise; the news split held in both historical periods (+0.00% / -0.37% vs +1.84% /
+1.97% per trade). The owner chose to keep trading news alerts; sizing them down is the softer version. Both periods
were used to build the confidence table, so only forward trades can test it.

## Rule (fixed)
Same trades as the live fade. Weight 1.5x when the trade's confidence (fade_texts.confidence at the alert time, with
the live news lookup) is >= 90, else 0.5x. Unknown news (lookup failed) uses the table's unknown row (93-99 -> 1.5x).

## Decision (forward)
Nightly shadow (`fadeconfsize` job) on the forward record; scored on the learning scoreboard. At forward n >= 300:
RECOMMEND only if weighted net per session beats equal weights with a session-bootstrap one-sided 95% lower bound > 0
(seed 20261106) and the forward mean weight is within 0.9-1.1. Competes with time-of-day and brain sizing; the owner
chooses at most one. Real sizing changes need the owner's OK and an amendment.
