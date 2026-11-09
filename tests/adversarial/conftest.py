"""The adversarial suites import the runner scripts (fade_live, sms_control, megacap_forward) directly.
2026-11-09: the deploy gate runs plain ``pytest tests`` (pythonpath = src only); without this, files that relied on a
sibling module inserting ``scripts/`` failed to collect depending on order."""
import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
