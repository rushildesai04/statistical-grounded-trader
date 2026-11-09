"""Learning scoreboard: every shadow challenger vs the live rule on forward trades (owner 2026-11-06: "keep making it
more intelligent, not stagnant").

    python scripts/learning_report.py          # nightly on the runner, after the shadow jobs

Reads each shadow's per-event file, computes the paired per-session difference vs the champion with a session
bootstrap at that study's pre-registered alpha, and marks a challenger READY when forward n reaches the decision point.
Writes reports/learning/scoreboard.md (+ .json) and a one-line summary the nightly digest shows. Promotion is never
automatic: a READY + beats line is a recommendation that needs the owner's OK and a prereg amendment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("reports")
OUT = ROOT / "learning"
DECIDE_N, SEED, N_BOOT = 300, 20261106, 10_000


@dataclass(frozen=True, slots=True)
class Source:
    dir: str
    prereg: str
    alpha: float
    kind: str                       # "cols": net_champion + net_<id> columns; "size": net and a size weight w


SOURCES = (Source("fade_challengers", "prereg_fade_challengers_2026-11-04", 0.05 / 4, "cols"),
           Source("fade_exits", "prereg_fade_exits_2026-11-05", 0.05 / 4, "cols"),
           Source("fade_hold", "prereg_fade_hold_2026-11-05", 0.05 / 2, "cols"),
           Source("fade_brain", "prereg_fade_brain_2026-11-05", 0.05, "size"),
           Source("fade_tod", "prereg_fade_tod_2026-11-06", 0.05, "size"),
           Source("fade_confsize", "prereg_fade_confsize_2026-11-06", 0.05, "size"),
           Source("fade_pricesize", "prereg_fade_pricesize_2026-11-06", 0.05, "size"),
           Source("fade_calmskip", "prereg_fade_calmskip_2026-11-08", 0.05, "size"),
           Source("fade_volup", "prereg_fade_calmskip_2026-11-08", 0.05, "size"),
           Source("fade_evsize", "prereg_fade_evsize_2026-11-09", 0.05, "size"),
           Source("megacap_overlay", "prereg_megacap_overlay_2026-11-06", 0.05, "cols"))


def challengers(t: pd.DataFrame, s: Source) -> dict[str, pd.DataFrame]:
    """Per challenger: a frame with session, champion net and challenger net (events both legs traded)."""
    if s.kind == "size":
        if not {"session", "net", "w"} <= set(t):
            return {}
        return {s.dir.replace("fade_", "") + " sizing": pd.DataFrame({"session": t.session, "c": t.net,
                                                                      "x": t.net * t.w})}
    out = {}
    for col in [c for c in t if c.startswith("net_") and c != "net_champion"]:
        f = pd.DataFrame({"session": t.session, "c": t.net_champion, "x": t[col]}).dropna()
        out[col[4:]] = f
    return out


def score(f: pd.DataFrame, alpha: float) -> dict:
    daily = f.groupby("session")[["c", "x"]].sum()
    diff = (daily.x - daily.c).to_numpy(float)
    if len(diff) < 2:
        return {"n": len(f), "sessions": len(diff), "champ": float(f.c.mean()), "chal": float(f.x.mean()),
                "diff": float(diff.mean()) if len(diff) else None, "lower": None, "upper": None}
    means = diff[np.random.default_rng(SEED).integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1)
    return {"n": len(f), "sessions": len(diff), "champ": float(f.c.mean()), "chal": float(f.x.mean()),
            "diff": float(diff.mean()), "lower": float(np.quantile(means, alpha)),
            "upper": float(np.quantile(means, 1 - alpha))}


def status(r: dict) -> str:
    if r["n"] < DECIDE_N:
        return f"collecting {r['n']}/{DECIDE_N}"
    if r["lower"] is not None and r["lower"] > 0:
        return "READY: beats the live rule (owner decision)"
    if r["upper"] is not None and r["upper"] < 0:
        return "READY: worse than the live rule (retire)"
    return "READY: no difference (keep the live rule)"


def build(root: Path = ROOT) -> list[dict]:
    rows = []
    for s in SOURCES:
        p = root / s.dir / "shadow.csv"
        if not p.exists():
            continue
        t = pd.read_csv(p)
        if t.empty or "session" not in t:
            continue
        t["session"] = t.session.astype(str)
        for name, f in challengers(t, s).items():
            r = score(f, s.alpha)
            rows.append({"challenger": name, "source": s.dir, "prereg": s.prereg, "alpha": s.alpha, **r,
                         "status": status(r)})
    return rows


def markdown(rows: list[dict], now: datetime) -> str:
    lines = [f"# Learning scoreboard ({now:%Y-%m-%d %H:%M} UTC)", "",
             "Forward (live) trades only. Champion = the live rule (short at the alert, 20% stop, 60 minutes). "
             f"A challenger is decided at n >= {DECIDE_N}; promotion needs the owner's OK.", "",
             "| challenger | study | n | live rule /trade | challenger /trade | diff per session [bound] | status |",
             "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -(r["diff"] or -9)):
        bound = f" [{r['lower']:+.2%}]" if r["lower"] is not None else ""
        diff = f"{r['diff']:+.2%}{bound}" if r["diff"] is not None else "-"
        lines.append(f"| {r['challenger']} | {r['prereg']} | {r['n']} | {r['champ']:+.2%} | {r['chal']:+.2%} | "
                     f"{diff} | {r['status']} |")
    return "\n".join(lines) + "\n"


def summary_line(rows: list[dict]) -> str | None:
    if not rows:
        return None
    n = max(r["n"] for r in rows)
    ready = [r for r in rows if r["status"].startswith("READY: beats")]
    lead = max(rows, key=lambda r: r["diff"] if r["diff"] is not None else -9)
    head = f"learning: {n}/{DECIDE_N} forward trades; "
    if ready:
        return head + "READY to promote: " + ", ".join(r["challenger"] for r in ready)
    return head + f"best so far {lead['challenger']} {lead['diff']:+.2%}/session vs live rule (not decided)"


def main() -> int:
    now = datetime.now(timezone.utc)
    rows = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scoreboard.md").write_text(markdown(rows, now))
    line = summary_line(rows)
    (OUT / "scoreboard.json").write_text(json.dumps({"asof": now.isoformat(), "rows": rows, "line": line}, indent=2))
    (OUT / "shadow_summary.json").write_text(json.dumps({"asof": now.isoformat(), "line": line}))   # digest reader
    print(line or "no shadow data yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
