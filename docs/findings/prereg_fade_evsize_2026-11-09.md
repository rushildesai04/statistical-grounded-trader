# Pre-registration: size each no-news fade by its expected value (forward shadow) — 2026-11-09

Committed BEFORE the model is fitted. Roadmap item 1 of `docs/research/architecture_audit_2026-11-09.md`: the fade
knows time of day, price band and pre-alert volatility, but trades every no-news alert at the same size.

## Inputs (point in time, known at entry)
- window: before 11:00 / 11:00-14:00 / after 14:00 ET (entry bar);
- price band: $1-2 / $2-5 / $5-10 / $10-50 (entry price);
- volatility group: calm (sigma < 0.015072) / middle / jumpy (sigma >= 0.029200), sigma = sd of 1-minute log returns
  over the last 30 bars that ended before the entry (`fade_volsize_study.sigma`; training quintile cut points).
- Population: NO-NEWS fades only (the live rule since F6); outcome = official net with REAL stop fills
  (`reports/fade_stops/fills.csv`).

## Model (fixed now, no tuning)
Additive ridge regression of net on one-hot groups (window, band, volatility; drop-one coding), ridge alpha 200
(shrinks each group effect toward the pooled mean; chosen before fitting), fitted once on TRAINING (2025-01-03 ..
2026-06-30). EV_hat = prediction. Weight w = clip(EV_hat / mean(EV_hat over training), 0.25, 2.0), then rescaled so the
training mean weight is 1 (same average capital). Coefficients frozen in `config/models/fade_ev_v1.json`.

## Evaluation
In-sample training numbers are reported but cannot validate (the model saw them); held-out 2026-07..10 is reported
but is descriptive (these factors were looked at there before). DECISION: forward shadow "ev sizing" on every live
no-news fade, scored by the learning scoreboard (paired per-session difference vs flat size, alpha 0.05), at n >= 300.
Promotion needs the owner's OK. Expected if real: more profit from the same average capital, mostly by shrinking
calm, morning, $2-5 trades and growing jumpy, afternoon, $1-2 ones.
