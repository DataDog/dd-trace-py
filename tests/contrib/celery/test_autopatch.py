#!/usr/bin/env python
import subprocess


def test_autopatch():
    """Test that celery is patched successfully if run with ddtrace-run."""
    out = subprocess.check_output(["ddtrace-run", "python", "tests/contrib/celery/autopatch.py"])
    assert out.startswith(b"Test success")
