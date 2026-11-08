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
