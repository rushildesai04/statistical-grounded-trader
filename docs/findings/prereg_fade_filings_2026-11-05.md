# Pre-registration: SEC filings at alert time vs the fade's result (2026-11-05)

Committed BEFORE any filing is downloaded for these events or any number computed.

## Why
The owner (2026-11-05): news is the most important input. Company news already splits the fade (prereg_fade_news:
no-news alerts +1.70%/trade dev, company-news alerts +0.02%). SEC filings are a second, official news source with an
exact acceptance time, free for every past event: an 8-K/6-K filed just before the alert marks a real corporate event
(earnings, deal, FDA), the kind of catalyst a fade should not fight; an S-1/S-3/F-1/F-3/424B filing in the prior 30
days means the company is selling (or ready to sell) shares into the move, extra supply that should help the fade.

## Data
Events: `reports/telegram/trades_pooled_first.csv`, status ok (dev = split train, 2,349; held-out = 764), scored by
`alert_fade_test.score` (net = the champion's). Filings: EDGAR submissions per CIK (`data.sec.gov`, ticker map
`company_tickers.json` as of today, older pages fetched when the recent list does not reach back far enough);
cached under `data/edgar/`. Point in time: only filings ACCEPTED strictly before the alert's `posted_at`.
Flags (`sgt.fast.filings.flags`, the same code the live texts use):
- `f_8k`: an 8-K or 6-K accepted after 16:00 ET of the previous weekday (the news study's window).
- `f_dil`: a form in `DILUTION_FORMS` (S-1, S-3, F-1, F-3 and amendments, 424B1-8) accepted in the prior 30 days.
Events whose ticker has no CIK today are "unknown" and reported separately (not counted as flag 0).

## Primary hypotheses and gates (development only)
H1 (skip): fades of `f_8k` events do worse. PASS when on dev events: (a) n(`f_8k`) >= 100; (b) mean net of `f_8k`
events < mean net of the rest, session-bootstrap one-sided upper bound of the difference < 0 at alpha 0.05/2;
(c) skipping `f_8k` events raises net per session vs take-all, paired session-bootstrap lower bound > 0 at 0.05/2.
H2 (size up): fades of `f_dil` events do better. PASS when on dev events: (a) n(`f_dil`) >= 100; (b) mean net of
`f_dil` events minus the rest > 0, session-bootstrap one-sided lower bound > 0 at alpha 0.05/2.
Bootstrap: 10,000 draws, seed 20261105, sessions resampled.

## Held-out and forward
Held-out was examined once for the champion and the news split: reported as SECONDARY, same statistics. A dev PASS
becomes a shadow challenger on forward events (H1: skip `f_8k`; H2: double size on `f_dil`), decided at forward
n >= 300 with a paired lower bound > 0. No live rule changes without the owner's OK and an amendment. Regardless of
the gates, the per-group win rate and mean (dev) are shown next to the filing line in the trade text, labelled as
history, like the news groups.

## Secondary (reported only)
Same table for: any filing in the prior 24 h; 10-Q/10-K in the prior 3 days; `f_8k` and `f_dil` together; the
news x filings cross (news_own from prereg_fade_news); coverage (share of events with a CIK).
Trial ledger run id `fade-filings-2026-11-05-dev`.

## Result (2026-11-05, dev run once, trial ledger `fade-filings-2026-11-05-dev`) — both gates FAIL
EDGAR submissions fetched for 1,077 of 1,280 event symbols (203 have no CIK under today's ticker map: renamed or
delisted). Coverage: dev 85%, held-out 98%. (A first `dev` run crashed on a dtype error inside the secondary table
before anything was printed or recorded; fixed and re-run.)

| dev 2025-01..2026-06 (known CIK) | n | mean net | win | rest mean | difference [bound] | gate |
|---|---|---|---|---|---|---|
| H1 8-K/6-K since yesterday 16:00 ET | 401 | +0.79% | 52% | +1.03% | -0.23% [upper +1.63%]; skipping them: -0.94%/session [lower -2.76%] | fail |
| H2 offering form in 30 days | 511 | +1.08% | 57% | +0.95% | +0.13% [lower -1.60%] | fail |

Held-out (secondary, already seen): 8-K -0.16% vs rest +1.36%; offering -1.80% vs rest +2.04% (opposite sign to
dev). News x 8-K (dev): no news & no 8-K +1.51% (n 1,054); no news & 8-K +3.92% (n 89); news & no 8-K +0.10%
(n 537); news & 8-K -0.10% (n 312).

Reading: filings add nothing reliable beyond the company-news split the fade already reports. The offering effect
flips sign between periods (noise); same-day 8-Ks look worse but not distinguishably, and skipping them would cost
money on dev. Nothing changes live. The trade text shows the filing line with this history, labelled, as context.
