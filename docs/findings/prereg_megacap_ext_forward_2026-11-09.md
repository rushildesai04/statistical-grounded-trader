# Pre-registration: extended forward paper test of daily per-ticker models (2026-11-09)

Committed BEFORE the first extended-set forward decision (2026-11-09 15:50 ET, run in batch after the
store accepts the session; see Mechanics). Uses only prices that do not exist yet. Sibling of
`prereg_megacap_forward_2026-10-30.md`, which forward-tests the original six names; this one adds the
other 15 names from the out-of-sample record (`config/models/model_tape.json`) as a **separate** forward
strategy so the six-name pre-registration stays untouched.

## What is tested
The identical daily system (`src/sgt/megacap/daily.py` + `walkforward.py`: LightGBM params, 10th/90th
percentile thresholds, monthly expanding-window retraining) and identical features — no new features, no
tuning — on the 15 names in the model tape that are NOT in the six-name test:

RDDT, TSLA, AMD, AVGO, COST, JPM, LLY, MA, NFLX, ORCL, UNH, V, WMT, XOM, AAOI.

These are the model tape's "development extra" (RDDT, TSLA), "unseen twelve" (AMD, AVGO, COST, JPM, LLY,
MA, NFLX, ORCL, UNH, V, WMT, XOM) and "trader's pick" (AAOI) groups. The historical out-of-sample record
is FAIL/marginal on all of them (section 6b of `docs/PLAYBOOK.md`); this forward test checks whether the
live decisions behave as the OOS record predicted, on the exact same code that produced that record.

## Mechanics (virtual ledger, no orders)
Identical to the six-name test, with its own directory `reports/megacap_ext/` so the two never share
files:
- Monthly model: before the first decision of month M, each ticker's model is trained on all rows with
  session < M (1-minute SIP store and adjusted daily bars refreshed first). Model + thresholds saved to
  `reports/megacap_ext/models/YYYY-MM/`. RDDT (IPO 2024-03-21) simply has fewer training rows.
- Daily decision (batch, prereg_megacap_forward amendment A1): features from adjusted daily bars through
  t-1 plus t's 1-minute SIP bars ending 15:49 ET and t's official open; written append-only to
  `reports/megacap_ext/decisions/YYYY-MM-DD.json`. Proof is reproducibility, not the clock: month models
  are trained only on rows whose labels were known before the month, saved append-only with SHA-256
  hashes checked on every load, and the decision code is the same tested point-in-time code as the
  six-name test. `scripts/megacap_forward.py auto --set ext` decides every session since the start date
  that has no decision yet.
- Fill: market-on-close at the official close of t, exit at the official close of t+1 (adjusted daily
  bars), minus 4 bp per round trip. Equal capital per ticker; portfolio day = mean over the 15.
- Paper book: the live decision is proposed to the approved paper book as strategy `megacap_ext`
  (`scripts/sms_control.py megacapext`), sized by `megacap_targets` under the same `MEGACAP_SIZING` caps
  as the six-name book (risk_per_trade 1%, max_position_pct 25%, gross_cap 1.0, beta_cap 1.0), trades
  automatically (auto_all). No timing overlay — that stays specific to the six-name overlay prereg.

## Evaluation
- Decided at **300 symbol-days** (15 names x ~20 sessions, about 4 weeks), the same cross-sectional
  sample size used elsewhere in the playbook to judge a forward record.
- At 300 symbol-days: CONTINUE as a forward test if cumulative portfolio net > 0; otherwise STOP the set.
  No name becomes eligible for real money on this test alone — real money still requires a strategy
  proven on unseen data and tradeable at a broker (playbook section 7).
- Stop at any time if cumulative net falls below -5%.
- Every monthly evaluation is recorded in the trial ledger (run id `megacap-ext-forward-2026-11-09`,
  kind `megacap-ext-forward`), separate from the six-name run id.

## Start
Start date 2026-11-09 (the day this file is committed). The first decision is made by the batch job after
the 1-minute store accepts that session (post_close 20:00 ET + 16 min) from the November models (trained
on sessions < 2026-11-01) and filled close t -> close t+1. No forward decision or forward price had been
seen when this was written.
