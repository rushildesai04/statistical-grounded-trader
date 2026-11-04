#!/bin/sh
# Gated deploy (2026-11-03): nothing reaches the Fly runner unless lint and the test suite pass, so a
# crash like the 11-02 digest (SessionBounds.close) is caught here instead of in the owner's texts.
set -eu
cd "$(dirname "$0")/../.."
ruff check src scripts tests --select E9,F63,F7,F82,F811   # runtime-error classes (style debt is not gated)
.venv/bin/python -m pytest tests -q --ignore=tests/research -p no:cacheprovider
fly deploy -c deploy/fly/fly.toml --remote-only
