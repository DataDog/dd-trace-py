"""Smoke test for fork_lifecycle_baseline.py, so the measurement script keeps working.

Only completion and the report shape are asserted; timings are informational.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parent / "fork_lifecycle_baseline.py"


@pytest.mark.parametrize("mode", ["bare", "ddtrace"])
def test_fork_lifecycle_baseline_runs(mode):
    env = dict(os.environ, DD_INSTRUMENTATION_TELEMETRY_ENABLED="true", DD_TRACE_AGENT_URL="http://127.0.0.1:1")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), mode, "--iterations", "8", "--storm", "16"],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.decode().strip().splitlines()[-1])
    assert report["mode"] == mode
    assert report["storm_os_fork"]["n"] == 16
    assert report["storm_os_fork_4_threads"]["n"] == 16
    if mode == "ddtrace":
        assert report["storm_os_fork"]["parent_recovery_s"] >= 0
