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
        # setup subprocess environment variables
        env = os.environ.copy()
        env["DD_RUNTIME_METRICS_ENABLED"] = str(self.runtime_metrics_enabled)

        # initialize subprocess args
        subp_cmd = []
        code = "import ddtrace; ddtrace.patch_all()\n"
        if self.with_ddtrace_run:
            subp_cmd = ["ddtrace-run"]
            code = ""

        if self.with_httpretty:
            # mock requests to the trace agent
            env["DD_TRACE_API_VERSION"] = "v0.4"
            code += """
import httpretty
from ddtrace import tracer
from ddtrace.internal.telemetry import telemetry_writer

httpretty.enable(allow_net_connect=False)
httpretty.register_uri(httpretty.PUT, '%s/%s' % (tracer.agent_trace_url, 'v0.4/traces'))
httpretty.register_uri(httpretty.POST, '%s/%s' % ('http://localhost:8126', telemetry_writer.ENDPOINT))
"""

        if self.telemetry_enabled:
            code += "telemetry_writer.enable()\n"

        if self.with_trace:
            code += "span = tracer.trace('test-x', service='bench-test'); span.finish()\n"

        # stage code for execution in a subprocess
        subp_cmd += [sys.executable, "-c", code]

        def _(loops):
            for _ in range(loops):
                subprocess.check_output(subp_cmd, env=env)

        yield _
