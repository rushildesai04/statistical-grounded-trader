"""Privileged control-plane ASGI app (arm/disarm, ladder stages, accounts, manual fills).

Deliberately separate from the public read-only signals app (``sgt.api.app``) so the
control surface can never ride along if the signals API is ever exposed beyond
loopback (2026-10-13, review S-1). Served only by ``scripts/run_control_plane.py``,
which binds 127.0.0.1 by default.
"""
from __future__ import annotations

from fastapi import FastAPI

from sgt.api.control import router as control_router

app = FastAPI(title="sgt-control-plane", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(control_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
