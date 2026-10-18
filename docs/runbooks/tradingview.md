# TradingView alert-webhook receiver

TradingView has no public market-data API. Its only supported outbound integration is
**alert webhooks**: when an alert fires, TradingView POSTs a user-authored message to a
public URL and expects a `2xx` response within roughly **3 seconds**. Requirements and
hard limits:

- A **paid** TradingView plan (webhooks are not on the free tier) with **2FA** enabled.
- The URL must be reachable on **port 80/443** over IPv4 (TradingView resolves A records;
  do not rely on IPv6-only or non-standard ports).
- TradingView **cannot sign** requests. Authentication is therefore a **shared secret
  embedded in the JSON body**, compared in constant time server-side.
- Keep handler work minimal: the receiver only validates and stores, so it answers well
  inside the 3 s timeout. On timeout TradingView may retry, which is why storage is
  idempotent on `(alert_name, ticker, bar time)`.

The receiver is `sgt.api.webhook_app` (a standalone ASGI app, never mounted on the
control-plane or signals apps). It exposes exactly one route, `POST /tradingview/alert`,
plus `GET /health`.

## 1. Store the shared secret

The secret is read via `sgt.core.secrets.get_secret("tradingview_webhook_secret")`
(OS keychain first, then `SGT_SECRET_TRADINGVIEW_WEBHOOK_SECRET`). Generate a long random
value and store it in the keychain:

```bash
python - <<'PY'
import secrets
from sgt.core.secrets import set_secret
set_secret("tradingview_webhook_secret", secrets.token_urlsafe(32))
PY
```

For CI/containers instead export `SGT_SECRET_TRADINGVIEW_WEBHOOK_SECRET=<value>`.
If no secret is configured the endpoint returns **503** (fail closed); a wrong secret
returns **401**. The secret is never logged or stored.

## 2. Run the receiver (loopback) behind a TLS tunnel

Do not expose the process on `0.0.0.0`. Bind loopback and front it with a TLS tunnel so
TLS termination, a stable hostname, and edge DDoS protection stay outside the process.

```bash
python scripts/run_webhook_receiver.py            # binds 127.0.0.1:8799
```

Then start a tunnel to the local port (pick one):

```bash
# Cloudflare Tunnel (recommended; stable hostname, no inbound firewall change)
cloudflared tunnel --url http://127.0.0.1:8799

# or ngrok
ngrok http 8799
```

Use the public `https://…` URL the tunnel prints as the TradingView webhook URL.
Binding a non-loopback host is refused unless `--i-understand-public-exposure` is passed.

Optional hardening flags:

- `--rate-capacity` / `--rate-refill-per-s` — per-source-IP token bucket (default 60 / 1 s;
  over-limit requests get **429**).
- `--max-body-bytes` — request body cap (default 8192; larger gets **413** before parsing).
- `--allow-ip IP_OR_CIDR` (repeatable) — restrict to specific source IPs/CIDRs. **[VERIFY]**
  TradingView publishes a set of webhook source IPs, but they are **not hardcoded here**
  because they change; confirm the current list from TradingView's own documentation before
  enabling, and expect to update it. Default: allow all.
- `--trust-forwarded-header x-forwarded-for` — take the client IP from a proxy header.
  Enable **only** behind a tunnel/proxy that overwrites that header, otherwise a caller can
  spoof its IP (defeating the allowlist and rate limiter). Cloudflare Tunnel sets
  `CF-Connecting-IP`; many proxies set `X-Forwarded-For`.

## 3. Alert-message template (paste into TradingView)

In the alert dialog, set the **Webhook URL** to your tunnel URL and the **Message** to a
JSON body. Use TradingView placeholders (`{{ticker}}`, `{{close}}`, `{{volume}}`,
`{{time}}`, `{{interval}}`, `{{exchange}}`) and Pine `plot`/`alert()` values for indicators.

Minimal signal alert:

```json
{
  "secret": "PASTE_YOUR_SECRET",
  "ticker": "{{ticker}}",
  "exchange": "{{exchange}}",
  "time": "{{time}}",
  "interval": "{{interval}}",
  "close": {{close}},
  "volume": {{volume}},
  "alert_name": "ema_cross"
}
```

Golden-check alert (carries TradingView's own indicator values via `plot`/`alert()` as
`{{plot_0}}`, `{{plot_1}}`, …). Field names must match the mapping table below:

```json
{
  "secret": "PASTE_YOUR_SECRET",
  "ticker": "{{ticker}}",
  "time": "{{time}}",
  "interval": "{{interval}}",
  "close": {{close}},
  "alert_name": "golden_ema_rsi",
  "indicators": { "ema9": {{plot_0}}, "rsi14": {{plot_1}}, "macd_hist": {{plot_2}}, "vwap": {{plot_3}} }
}
```

Body schema (strict; unknown fields are rejected with **422**):

| field | type | notes |
| --- | --- | --- |
| `secret` | string | required; shared secret, constant-time compared |
| `ticker` | string | required; `[A-Z0-9.-]`, ≤16 chars, upper-cased |
| `time` | string or int | required; ISO-8601 (trailing `Z` ok) or epoch **milliseconds** |
| `exchange` | string? | optional, ≤32 |
| `interval` | string? | optional, ≤16 (e.g. `"1"`, `"5"`, `"D"`) |
| `close` | number? | optional, > 0 |
| `volume` | number? | optional, ≥ 0 |
| `alert_name` | string | required, ≤128; part of the idempotency key |
| `indicators` | object? | optional `{name: number}`, ≤32 entries |

`time` is the alert's **bar time** and forms the idempotency key with `alert_name` and
`ticker`. Note: TradingView's `{{time}}` is the bar open time of the alerting bar.

## 4. How alerts are used

- **Candidate source.** `sgt.crowd.tradingview.recent_tradingview_symbols(session, since)`
  returns the distinct tickers seen since a cutoff, mirroring the Telegram-alert symbol
  feed for the scanner/watchlist.
- **Golden checks.** Alerts carrying indicator values let us verify our indicator math
  against TradingView's.

## 5. Golden check (do our indicators match TradingView?)

`scripts/tradingview_golden_check.py` recomputes each mapped indicator from the Parquet bar
store using the live streaming code path and reports abs/rel differences with pass/fail.

Name mapping (`sgt.crowd.tradingview.INDICATOR_MAP`):

| alert name | our indicator output |
| --- | --- |
| `ema9` / `ema20` / `ema50` / `ema200` | `ema(n=…).value` |
| `sma20` / `sma50` | `sma(n=…).value` |
| `rsi14` | `rsi(n=14).value` |
| `macd_line` / `macd_signal` / `macd_hist` | `macd(fast=12,slow=26,signal=9).{line,signal,hist}` |
| `vwap` | `vwap(anchor=session).value` |

```bash
python scripts/tradingview_golden_check.py --since 2026-10-01 --lookback-sessions 2 \
    --data-dir data/alpaca --abs-tol 1e-4 --rel-tol 1e-3 --out golden.md
```

Exit code is non-zero if any comparison fails or an alert could not be checked (unknown
bar, indicator not yet warmed up), so it can gate CI. `vwap(anchor=session)` and the EMAs
depend on where the bar stream starts: `--lookback-sessions` warms up recursive indicators
with prior sessions, but TradingView anchors session VWAP to the RTH/ETH session and uses
its own full history for EMAs, so expect small residuals and tune the tolerances rather
than demanding bit-exactness across venues.

## 6. Limits recap

Paid plan + 2FA · public IPv4 URL on 80/443 · ~3 s response budget · no request signing
(shared-secret auth only) · body ≤ 8 KB · idempotent per `(alert_name, ticker, bar time)`.
