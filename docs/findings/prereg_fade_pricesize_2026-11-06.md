# Pre-registration: size the fade by entry price — forward only (2026-11-06)

Committed BEFORE any forward number for this rule is computed.

## Why
By entry price, in both historical periods (PLAYBOOK finding 12): $1-2 fades +2.4% / +2.1% per trade (about two thirds
of all profit), $2-5 +0.2% / +0.2%, $5-10 +0.9% / +1.2%, $10-50 +0.3% / +0.8%. The pattern was found by looking at
both periods, so only forward trades can test it.

## Rule (fixed)
Same trades as the live fade. Weight by the official entry price: 1.5x for $1-2, 0.5x for $2-5, 1.0x otherwise
(historical mean weight about 0.95).

## Decision (forward)
Nightly shadow (`fadepricesize` job), scored on the learning scoreboard. At forward n >= 300: RECOMMEND only if weighted
net per session beats equal weights with a session-bootstrap one-sided 95% lower bound > 0 (seed 20261106) and the
forward mean weight is within 0.9-1.1. Competes with the other sizing shadows (brain, time of day, confidence); the
owner chooses at most one. Real sizing changes need the owner's OK and an amendment.
