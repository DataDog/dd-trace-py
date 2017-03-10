#!/usr/bin/env python
import os
import sys

import subprocess
import unittest


class DdtraceRunTest(unittest.TestCase):
    def tearDown(self):
        """
        Clear DATADOG_* env vars between tests
        """
        for k in ('DATADOG_ENV', 'DATADOG_TRACE_ENABLED', 'DATADOG_SERVICE_NAME', 'DATADOG_TRACE_DEBUG'):
            if k in os.environ:
                del os.environ[k]

    def test_service_name_default(self):
        """
        In the absence of $DATADOG_SERVICE_NAME, use a default service derived from command-line
        """
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_service_default.py']
        )
        assert out.startswith(b"Test success")

    def test_service_name_passthrough(self):
        """
        When $DATADOG_SERVICE_NAME is present don't override with a default
        """
        os.environ["DATADOG_SERVICE_NAME"] = "my_test_service"

        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_service.py']
        )
        assert out.startswith(b"Test success")

    def test_env_name_passthrough(self):
        """
        $DATADOG_ENV gets passed through to the global tracer as an 'env' tag
        """
        os.environ["DATADOG_ENV"] = "test"
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_env.py']
        )
        assert out.startswith(b"Test success")

    def test_env_enabling(self):
        """
        DATADOG_TRACE_ENABLED=false allows disabling of the global tracer
        """
        os.environ["DATADOG_TRACE_ENABLED"] = "false"
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_disabled.py']
        )
        assert out.startswith(b"Test success")

        os.environ["DATADOG_TRACE_ENABLED"] = "true"
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_enabled.py']
        )
        assert out.startswith(b"Test success")

    def test_patched_modules(self):
        """
        Using `ddtrace-run` registers some generic patched modules
        """
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_patched_modules.py']
        )
        assert out.startswith(b"Test success")

    def test_integration(self):
        out = subprocess.check_output(
            ['ddtrace-run', 'python', '-m', 'tests.commands.ddtrace_run_integration']
        )
        assert out.startswith(b"Test success")

    def test_debug_enabling(self):
        """
        DATADOG_TRACE_DEBUG=true allows setting debug_logging of the global tracer
        """
        os.environ["DATADOG_TRACE_DEBUG"] = "false"
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_no_debug.py']
        )
        assert out.startswith(b"Test success")

        os.environ["DATADOG_TRACE_DEBUG"] = "true"
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_debug.py']
        )
        assert out.startswith(b"Test success")

    def test_host_port_from_env(self):
        """
        DATADOG_TRACE_AGENT_HOSTNAME|PORT point to the tracer
        to the correct host/port for submission
        """
        os.environ["DATADOG_TRACE_AGENT_HOSTNAME"] = "172.10.0.1"
        os.environ["DATADOG_TRACE_AGENT_PORT"] = "58126"
        out = subprocess.check_output(
            ['ddtrace-run', 'python', 'tests/commands/ddtrace_run_hostname.py']
        )
        assert out.startswith(b"Test success")
