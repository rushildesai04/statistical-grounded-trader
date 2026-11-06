"""Build the Model Tape page from the daily model's out-of-sample record (owner 2026-11-06).

    python scripts/model_tape.py --out reports/model_tape/model_tape.html [--fragment]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from sgt.ops import model_tape

SOURCES = (("original six", Path("reports/megacap_daily")), ("unseen twelve", Path("reports/megacap_oos")))
ORIGINAL = {"AAPL", "AMZN", "GOOGL", "MSFT", "META", "NVDA"}


def forward_rows(path: Path | None) -> list[dict]:
    """Live calls from the runner's reports/megacap_forward/marks.csv (position at 15:50 ET, next close-to-close)."""
    if not path or not path.exists():
        return []
    m = pd.read_csv(path)
    m = m[~m.void.astype(bool)] if "void" in m else m
    return [{"date": str(r.date), "symbol": str(r.symbol), "position": int(r.position),
             "ret": model_tape._f(r.ret), "net": model_tape._f(r.net)} for r in m.itertuples(index=False)]


def build(fragment: bool = False, forward: Path | None = None) -> str:
    views = []
    for group, root in SOURCES:
        for f in sorted(root.glob("oos_*.parquet")):
            sym = f.stem.removeprefix("oos_")
            daily = Path("data/daily") / f"{sym}.parquet"
            if not daily.exists():
                continue
            g = group if group != "original six" or sym in ORIGINAL else "development extra"
            views.append(model_tape.view(sym, g, pd.read_parquet(f), pd.read_parquet(daily)))
    html = model_tape.render(views, datetime.now(timezone.utc), forward_rows(forward))
    if fragment:
        return html
    head = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')
    return head + html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("reports/model_tape/model_tape.html"))
    ap.add_argument("--fragment", action="store_true", help="omit the document head (for an Artifact publish)")
    ap.add_argument("--forward", type=Path, default=Path("reports/megacap_forward/marks.csv"),
                    help="live marks (runner path by default)")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp")
    tmp.write_text(build(a.fragment, a.forward))
    tmp.replace(a.out)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
