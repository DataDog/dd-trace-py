"""Fork lifecycle contract on the real Celery beat path.

Celery beat forks and then closes every inherited descriptor through ``celery.platforms.close_open_fds``,
which the integration brackets with the Python before-fork and after-in-parent hooks. The scenario
runs in its own interpreter (see ``fork_lifecycle_beat.py``) because the native fork handlers are
process-global. The contract itself is described in ``tests/internal/test_fork_lifecycle_contract.py``.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parent / "fork_lifecycle_beat.py"


@pytest.fixture(scope="module")
def beat_report(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("beat")
    child_path = tmp / "child.json"
    env = dict(os.environ)
    env.update(
        DD_INSTRUMENTATION_TELEMETRY_ENABLED="true",
        # Nothing listens on this port, so every export fails fast.
        DD_TRACE_AGENT_URL="http://127.0.0.1:1",
        DD_REMOTE_CONFIGURATION_ENABLED="false",
        PYTHONWARNINGS="ignore::DeprecationWarning",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(child_path), str(tmp)],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    stderr = proc.stderr.decode(errors="replace")
    lines = proc.stdout.decode(errors="replace").strip().splitlines()
    assert proc.returncode == 0 and lines, stderr
    child = json.loads(child_path.read_text()) if child_path.exists() else {}
    return {"parent": json.loads(lines[-1]), "child": child, "stderr": stderr}


def test_beat_process_is_safe(beat_report):
    parent = beat_report["parent"]
    assert parent["child_reported"], beat_report
    assert parent["beat_exitcode"] == 0, beat_report
    assert "panic" not in beat_report["stderr"].lower(), beat_report["stderr"]


def test_beat_parent_resumes(beat_report):
    parent = beat_report["parent"]
    assert parent["periodic_resumed"], parent
    assert parent["native_flush"] == "ok", parent


def test_beat_child_functional(beat_report):
    child = beat_report["child"]
    assert child["functional"] == "ok", child
    assert child["native_restartable_running"], child


def test_beat_child_restarts_periodic_service(beat_report):
    assert beat_report["child"]["periodic_restarted"], beat_report["child"]


def test_beat_child_keeps_non_restartable_service_stopped(beat_report):
    """A service created with autorestart=False must not come back in the beat child."""
    assert beat_report["child"]["not_restartable_stayed_stopped"], beat_report["child"]
