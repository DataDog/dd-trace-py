import os
import subprocess
import sys

import bm


class Startup(bm.Scenario):
    with_ddtrace_run = bm.var_bool()
    runtime_metrics_enabled = bm.var_bool()
    with_httpretty = bm.var_bool()
    telemetry_enabled = bm.var_bool()
    with_trace = bm.var_bool()

    def run(self):
        env = os.environ.copy()
        env["DD_RUNTIME_METRICS_ENABLED"] = str(self.runtime_metrics_enabled)
        env["DD_TRACE_AGENT_URL"] = "http://localhost:8126"
        env["DD_TRACE_API_VERSION"] = "v0.4"

        code = "import ddtrace"
        if self.with_httpretty:
            code += """
import httpretty
httpretty.enable(allow_net_connect=False)

telemetry_url =  '%s/%s' % (
    'http://localhost:8126',
    ddtrace.internal.telemetry.telemetry_writer.ENDPOINT
)
httpretty.register_uri(httpretty.POST, telemetry_url)

httpretty.register_uri(httpretty.PUT, 'http://localhost:8126/v0.4/traces')
"""

        if self.telemetry_enabled:
            code += "\nddtrace.internal.telemetry.telemetry_writer.enable()"

        if self.with_trace:
            code += "\nspan = ddtrace.tracer.trace('test-x', service='bench-test'); span.finish()"

        cmd = [sys.executable, "-c", code]
        if self.with_ddtrace_run:
            cmd = ["ddtrace-run"] + cmd

        def _(loops):
            for _ in range(loops):
                subprocess.check_output(cmd, env=env)

        yield _
