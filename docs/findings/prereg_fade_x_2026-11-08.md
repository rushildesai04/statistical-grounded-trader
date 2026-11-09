# Pre-registration: X (Twitter) buzz at the alert vs the fade's result — forward only (2026-11-08)

Committed BEFORE any X data is recorded. Owner decision 2026-11-08: add the X API next to StockTwits.

## Data
`sgt.fast.xsocial`: recent search for the cashtag over the hour before the alert (end 15 s before it), stamped on
every live fade as `x_n60`, `x_n15`, `x_authors`, `x_eng`. Pay-per-use: one request per alert, <= 100 posts, at most 60
requests a day. Dormant until the owner's bearer token is stored (keychain `sgt/x_bearer`, Fly
`SGT_SECRET_X_BEARER`).

## Hypothesis and test (once, at >= 300 live fades with X data, >= 100 per group)
H: fades of HIGH-buzz alerts earn more (the attention mechanism). High = `x_n60` at or above the median of recorded
trades at the decision point. Mean official net (high) - (low), session bootstrap (10,000, seed 20261108), one-sided
95% lower bound > 0 -> PASS -> a sizing shadow is pre-registered separately. Unknown (no key, capped, error) is
reported apart, never as low buzz. Secondary: agreement with StockTwits buzz; unique authors; engagement.

## Amendment A1 (2026-11-08, before any X data was recorded: 0 rows with `x_n60` on the runner)
Owner: spend as little as possible for the most information. X bills search per post returned ($0.005 each, so a
100-post page costs up to $0.50) and counts per request ($0.005). New collection, per alert:
- `x_n60`, `x_n15` from `/2/tweets/counts/recent` (minute buckets): exact and uncapped (the 100-post page saturated
  on busy names, e.g. a TSLA test returned 100). The primary measure is unchanged in meaning and now measured better.
- `x_authors`, `x_eng` now over the NEWEST <= 10 posts only (API minimum page), fetched only when `x_n60` > 0; NULL
  when that call fails. Secondary measures only; reported as "among the newest 10".
- One lookup per symbol per 10 minutes (a second text for the same trade reuses it); at most 60 paid lookups a day.
Worst case $0.055 per lookup (was $0.50). Hypothesis, test, threshold and decision point are unchanged.

## Amendment A2 (owner 2026-11-09, before any catalyst data is recorded): targeted catalyst check
Owner: use X "correctly and wisely with optimal searches", not blind collection. The random newest-10 sample (accounts,
likes) is dropped; X now answers two narrow questions at ENTRY for trades the book takes (`sgt.fast.xsocial`):
attention (`x_n60`, `x_n15`, unchanged) and **catalyst**: `x_cat24` = posts in the 24 hours before entry with the
cashtag AND a catalyst word (announces, "press release", FDA, approval, contract, merger, acquisition, acquire,
offering, earnings, partnership, agreement, patent, clinical, "trial results"; retweets excluded), plus the <= 5 most
relevant of them (`x_cat_posts`, display and AI review only). Cost <= $0.06 per trade.
Secondary hypothesis (forward only, decided with the primary at >= 300 live fades with X data): among fades our
Benzinga filter calls NO-NEWS, those with `x_cat24` >= 3 earn less than those with `x_cat24` = 0 (a catalyst the news
feed missed). Session bootstrap one-sided 95%, >= 60 per group; PASS -> a skip rule is proposed to the owner.
