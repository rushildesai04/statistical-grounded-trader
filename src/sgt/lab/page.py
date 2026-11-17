"""The lab page: replays -> one self-contained HTML file (data embedded; the only outside requests are the pinned
chart library and fonts, as on the Fade Tape page). Text from alerts and symbols is untrusted: it is embedded as JSON
with ``<`` escaped and written by the page with ``textContent`` only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sgt.lab.replay import stamp
from sgt.ops.charts import DOC_HEAD, finite

TEMPLATE = Path(__file__).with_name("lab.html")
MAX_REPLAYS = 80                              # about 25 KB each; keeps the file a few MB at most
MAX_BYTES = 8 * 1024 * 1024


def render(replays: list[dict], generated: datetime) -> str:
    data = {"generated": stamp(generated), "replays": replays[:MAX_REPLAYS]}
    payload = json.dumps(finite(data), allow_nan=False).replace("<", "\\u003c")   # untrusted text cannot end the script
    page = DOC_HEAD + TEMPLATE.read_text().replace("/*LAB*/null", payload, 1)
    if len(page.encode()) > MAX_BYTES:
        raise ValueError("lab page too large: fewer replays")
    return page
