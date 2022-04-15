import os
import subprocess
import sys

import bm


class Startup(bm.Scenario):
    telemetry_enabled = bm.var_bool()
    use_ddtrace_run = bm.var_bool()
    with_trace = bm.var_bool()

    def run(self):
        code = "from ddtrace import tracer"
        if self.with_trace:
            # queue and encode trace but don't send to the agent
            # requests to the agent will raise an exception in a benchmark test
            code += """
import mock
tracer._writer._send_payload = mock.Mock()
with tracer.trace('test-x', service='bench-test'):
    pass
"""

        cmd = [sys.executable, "-c", code]
        if self.use_ddtrace_run:
            cmd = ["ddtrace-run"] + cmd

        env = os.environ.copy()
        env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = str(self.telemetry_enabled)

        def _(loops):
            for _ in range(loops):
                subprocess.check_output(cmd, env=env)

        yield _
