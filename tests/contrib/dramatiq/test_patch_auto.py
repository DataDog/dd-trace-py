#!/usr/bin/env python
import subprocess


def test_autopatch():
    """Test that dramatiq is patched successfully if run with ddtrace-run."""
    out = subprocess.check_output(args=["ddtrace-run", "python", "tests/contrib/dramatiq/autopatch.py"])
    assert out.startswith(b"Test success")
