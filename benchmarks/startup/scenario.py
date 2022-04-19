import os
import subprocess
import sys

import bm


class Startup(bm.Scenario):
    telemetry_enabled = bm.var_bool()
    runtime_metrics_enabled = bm.var_bool()
    use_ddtrace_run = bm.var_bool()
    with_trace = bm.var_bool()

    def run(self):
        env = os.environ.copy()
        env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = str(self.telemetry_enabled)
        env["DD_RUNTIME_METRICS_ENABLED"] = str(self.runtime_metrics_enabled)

        code = "import ddtrace"
        if self.with_trace:
            # mock agent endpoint
            env["DD_TRACE_AGENT_URL"] = "http://localhost:8126"
            env["DD_TRACE_API_VERSION"] = "v0.4"
            code += """
import httpretty
# mock agent endpoint
httpretty.enable()
httpretty.register_uri(httpretty.PUT, 'http://localhost:8126/v0.4/traces')

with ddtrace.tracer.trace('test-x', service='bench-test'):
    pass
"""

        cmd = [sys.executable, "-c", code]
        if self.use_ddtrace_run:
            cmd = ["ddtrace-run"] + cmd

        def _(loops):
            for _ in range(loops):
                subprocess.check_output(cmd, env=env)

        yield _
