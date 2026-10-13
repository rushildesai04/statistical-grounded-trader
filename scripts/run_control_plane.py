"""Run the control-plane API bound to a loopback host.

The control plane exposes arming/disarming and other state-changing operations, so it
MUST NOT be publicly exposed (docs/IMPLEMENTATION_PLAN.md §14). By default it binds to
``settings.control_plane_host`` (127.0.0.1). Binding to a non-loopback address (e.g.
0.0.0.0) is refused unless ``--i-understand-public-exposure`` is passed explicitly, and
even then only alongside the bearer-token auth every endpoint enforces.

Usage::

    python scripts/run_control_plane.py            # binds 127.0.0.1:8787
    python scripts/run_control_plane.py --port 9000
    python scripts/run_control_plane.py --host 0.0.0.0 --i-understand-public-exposure
"""
from __future__ import annotations

import argparse

from sgt.config import get_settings

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run the SGT control-plane API (loopback by default).")
    ap.add_argument("--host", default=None, help="bind host (default: settings.control_plane_host)")
    ap.add_argument("--port", type=int, default=8787, help="bind port (default: 8787)")
    ap.add_argument(
        "--i-understand-public-exposure",
        action="store_true",
        help="allow binding to a non-loopback host (DANGEROUS; control plane is privileged)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    host = args.host or settings.control_plane_host
    if host not in _LOOPBACK and not args.i_understand_public_exposure:
        raise SystemExit(
            f"refusing to bind the control plane to non-loopback host {host!r}; "
            "pass --i-understand-public-exposure to override"
        )
    import uvicorn

    uvicorn.run("sgt.api.control_app:app", host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
