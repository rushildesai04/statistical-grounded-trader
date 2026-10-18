"""Run the TradingView alert-webhook receiver, bound to loopback by default.

TradingView requires a public HTTPS URL it can POST alerts to. Do NOT expose this
process directly on 0.0.0.0; instead bind loopback and put a TLS tunnel in front
(Cloudflare Tunnel or ngrok) so TLS termination, a stable hostname, and DDoS edge
protection live outside the process. See docs/runbooks/tradingview.md.

Binding to a non-loopback host is refused unless ``--i-understand-public-exposure``
is passed explicitly (mirrors scripts/run_control_plane.py). Authentication is the
shared secret in each alert body (``tradingview_webhook_secret``), verified in the app.

Usage::

    python scripts/run_webhook_receiver.py                      # 127.0.0.1:8799
    python scripts/run_webhook_receiver.py --port 9001
    python scripts/run_webhook_receiver.py --allow-ip 1.2.3.4/32 --allow-ip 5.6.7.0/24
    python scripts/run_webhook_receiver.py --trust-forwarded-header x-forwarded-for
"""
from __future__ import annotations

import argparse

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Run the TradingView alert-webhook receiver (loopback by default)."
    )
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8799, help="bind port (default: 8799)")
    ap.add_argument(
        "--i-understand-public-exposure",
        action="store_true",
        help="allow binding to a non-loopback host (DANGEROUS; prefer a TLS tunnel)",
    )
    ap.add_argument("--rate-capacity", type=float, default=60.0, help="token-bucket capacity per IP")
    ap.add_argument("--rate-refill-per-s", type=float, default=1.0, help="tokens refilled per second per IP")
    ap.add_argument("--max-body-bytes", type=int, default=8192, help="request body size cap (bytes)")
    ap.add_argument(
        "--allow-ip",
        action="append",
        default=[],
        metavar="IP_OR_CIDR",
        help="restrict to these source IPs/CIDRs (repeatable; default: allow all). "
        "TradingView's published IPs are unverified here — confirm before use.",
    )
    ap.add_argument(
        "--trust-forwarded-header",
        default=None,
        help="read client IP from this header's first entry (ONLY behind a trusted proxy/tunnel)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.host not in _LOOPBACK and not args.i_understand_public_exposure:
        raise SystemExit(
            f"refusing to bind the webhook receiver to non-loopback host {args.host!r}; "
            "prefer a TLS tunnel, or pass --i-understand-public-exposure to override"
        )

    from sgt.api.webhook_app import create_app

    app = create_app(
        rate_capacity=args.rate_capacity,
        rate_refill_per_s=args.rate_refill_per_s,
        max_body_bytes=args.max_body_bytes,
        allowlist=tuple(args.allow_ip),
        trust_forwarded_header=args.trust_forwarded_header,
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
