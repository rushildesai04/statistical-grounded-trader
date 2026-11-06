# Pre-registration: size the fade by time of day — forward only (2026-11-06)

Committed BEFORE any forward number for this rule is computed.

## Why (and why forward only)
prereg_fade_hold_2026-11-05 (secondary table) showed, in BOTH the training and the held-out periods, that alerts after
11:00 ET earned far more per trade than morning alerts: champion net per trade 09:30-11:00 +0.58% / +0.39%,
11:00-14:00 +1.63% / +1.89%, 14:00-16:00 +2.33% / +4.04% (training / held-out). The pattern was found by looking at
both periods, so neither can test it; only forward events can.

## Rule (fixed)
Same trades as the champion. Size weight by the alert's entry time (ET): 0.75x for entries before 11:00, 1.5x from
11:00 on. (Training mix: about two thirds morning, so the average weight is about 1.0; the forward average is
reported so hidden leverage is visible.)

## Decision (forward)
Nightly shadow (`fadetod` job) on the forward record (amendment F1 events, official SIP nets). At forward n >= 300:
RECOMMEND time-of-day sizing only if weighted net per session beats equal weights with a session-bootstrap one-sided
95% lower bound of the paired difference > 0 (10,000 resamples, seed 20261106) AND the forward mean weight is within
0.9-1.1. Real sizing changes need the owner's OK and an amendment. Reported next to the fade brain sizing (both are
challengers to equal sizing; the owner chooses at most one).
Trial ledger run id `fade-tod-forward` (written at the decision point).

## Note (2026-11-06)
This file was written before any forward number was computed, but committed a minute after the first local shadow
run (29 forward events: equal -1.10%/trade, sized +0.03%/trade, mean size 1.09x; pre-11 n16 -4.1%, 11+ n13 +2.5%).
The rule above was not changed after that run. The decision is at n >= 300.
