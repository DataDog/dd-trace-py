#!/usr/bin/env python
import os

import subprocess
import unittest


class OTInstalledDDTraceRunTest(unittest.TestCase):
    """Test the ddtrace-run command when OpenTracing is installed."""
    def tearDown(self):
        """
        Clear DATADOG_* env vars between tests
        """
        for k in ('DATADOG_ENV', 'DATADOG_TRACE_ENABLED', 'DATADOG_SERVICE_NAME', 'DATADOG_TRACE_DEBUG'):
            if k in os.environ:
                del os.environ[k]

    def test_patch(self):
        """Since OpenTracing should be installed, the Datadog opentracer should
        be installed to `opentracing.tracer`.
        """
        os.environ["DATADOG_SERVICE_NAME"] = "svc"

        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ot_ddtrace_patch.py']
        )
        assert out.startswith(b"Test success")

    def test_service_name(self):
        os.environ["DATADOG_SERVICE_NAME"] = "svc"

        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ot_ddtrace_run_service.py']
        )
        assert out.startswith(b"Test success")
