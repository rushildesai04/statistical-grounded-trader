# Pre-registration: Reddit buzz at the alert vs the fade's result — forward only (2026-11-08)

Committed BEFORE any Reddit data is recorded. Owner decision 2026-11-08: add Reddit next to StockTwits and X.

## Data
`sgt.fast.reddit`: ApeWisdom's free public API (Reddit's own Data API needs per-app approval and reportedly closed to
new requests on 2026-10-31). Filter `all-stocks` (Reddit stock subreddits), about 700 ranked tickers, mentions and
upvotes over the last 24 h and mentions 24 h earlier. Stamped on every live fade as `rd_rank`, `rd_mentions`,
`rd_upvotes`, `rd_mentions_prev`. Not in the table = `rd_rank` NULL, `rd_mentions` 0 (not discussed). Any failed page
= unknown (all NULL), never "not discussed". Table fetched at most once per 10 minutes; free, no key.

## Hypothesis and test (once, at >= 300 live fades with Reddit data)
H: fades of alerts already discussed on Reddit (`rd_mentions` > 0) earn more than unlisted ones (retail crowding ->
overextension). Mean official net (listed) - (unlisted), session bootstrap (10,000, seed 20261108), one-sided 95% lower
bound > 0 with >= 100 trades per group -> PASS -> a sizing shadow is pre-registered separately. If fewer than 100 are
listed at 300, the test waits for 100 (reported, not run). Unknown is reported apart.
Secondary: rising buzz (`rd_mentions` > 2 x `rd_mentions_prev`); agreement with StockTwits and X buzz.

## Limits
24-hour window (coarser than StockTwits / X's hour). Third-party counts (ApeWisdom's subreddit list and ticker
matching), recorded as-is.
