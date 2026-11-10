# Review of the AI analysts' proposed tests (2026-11-10)

18 proposed tests from four Sol/Luna runs (trade_insights ids 1-4), grouped into 8 themes. None needs a new
pre-registration: each is already live, already tested, or blocked on data we do not have.

| theme (times proposed) | verdict | evidence |
|---|---|---|
| Point-in-time company-news eligibility (4) | done | Amendment F6 (no-news only, checked at entry, owner 2026-11-09); `prereg_fade_news_2026-11-05` |
| Trade only when borrow is shown available (3) | blocked on data | Historical borrow needs an IBKR account (owner declined). Every live trade is stamped with IBKR/Alpaca borrow (`fade_trades.ib_*`, `alp_*`); the digest shows the borrowable-only record nightly |
| Tail-risk-normalised sizing (1) | tested, rejected | Inverse-volatility sizing FAIL (`prereg_fade_volsize_2026-11-08`); size is the owner's SIZE setting; F7 (no shorts on beta >= 3) removes the worst tail group |
| Skip halt-exposed entries (3) | done | `prereg_fade_halt_audit_2026-11-09`; `fade.entry_start` (no entry without a bar within 5 min) since 2026-11-09 |
| Fill / outcome reconciliation audit (4) | done | The nightly settle recomputes every trade from SIP bars with the research code (`fade_live.official`); live-vs-official gaps reported nightly (`sgt.fast.realism`) |
| Partial profit at -5% (2) | tested, rejected | Partial profit is one of the rejected exit rules (PLAYBOOK section 4; `prereg_fade_exits_2026-11-05` family) |
| Maximum-adverse-excursion cohort report (1) | rejected | Report only, no decision; the path is covered by the exit studies (finding 3) |
| Megacap position-ledger reconstruction (1) | done | `sgt.ops.book` marks target-weight states, not independent trades; the digest shows the final position per symbol (#68) |

Marked reviewed with `python scripts/trade_analyst.py --reviewed` on the runner.
