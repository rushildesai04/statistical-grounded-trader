"""Deploy wiring guard (2026-11-06): every scheduled job has an entrypoint case, a plain-English failure text and an
existing script, so a new nightly job cannot ship half-wired."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ops_notify  # noqa: E402

SILENT = {"sms"}        # the per-minute SMS poll never pages by design (entrypoint.sh comment); it logs to poll.log


def cron_jobs() -> set[str]:
    jobs = set()
    for line in (ROOT / "deploy/fly/crontab").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            m = re.search(r"entrypoint\.sh job (\w+)", line)
            if m:
                jobs.add(m.group(1))
    return jobs


def entry_cases() -> dict[str, str]:
    """``job`` case label -> its command text (cases may span lines until ``;;``)."""
    text = (ROOT / "deploy/fly/entrypoint.sh").read_text()
    return {m.group(1): m.group(2) for m in re.finditer(r"^\s{4}(\w+)\)\s+(.*?);;", text, flags=re.M | re.S)}


def test_every_scheduled_job_is_wired():
    jobs, cases = cron_jobs(), entry_cases()
    assert jobs and jobs <= set(cases), f"cron jobs without an entrypoint case: {sorted(jobs - set(cases))}"
    missing_text = sorted(j for j in jobs - SILENT if j not in ops_notify.JOBS)
    assert not missing_text, f"jobs without an ops_notify failure text: {missing_text}"


def test_every_entrypoint_case_runs_an_existing_script():
    for job, cmd in entry_cases().items():
        for script in re.findall(r"/app/scripts/([\w.]+\.py)", cmd):
            assert (ROOT / "scripts" / script).exists(), f"{job}: scripts/{script} does not exist"
