#!/usr/bin/env python
import subprocess
import unittest


class DdtraceRunTest(unittest.TestCase):
    """Test that dramatiq is patched successfully if run with ddtrace-run."""

    def test_autopatch(self):
        out = subprocess.check_output(args=["ddtrace-run", "python", "tests/contrib/dramatiq/autopatch.py"])
        assert out.startswith(b"Test success")
