import os
import subprocess
import sys

import bm


class DDtraceRun(bm.Scenario):
    ddtrace_run: bool
    http: bool
    runtimemetrics: bool
    telemetry: bool
    profiling: bool
    appsec: bool
    tracing: bool

    def run(self):
        # setup subprocess environment variables
        env = os.environ.copy()
        env["DD_RUNTIME_METRICS_ENABLED"] = str(self.runtimemetrics)
        env["DD_APPSEC_ENABLED"] = str(self.appsec)

        # initialize subprocess args
        subp_cmd = []
        code = "import ddtrace; ddtrace._monkey._patch_all()\n"
        if self.ddtrace_run:
            subp_cmd = ["ddtrace-run"]
            code = ""

        if self.http:
            # mock requests to the trace agent before starting services
            env["DD_TRACE_API_VERSION"] = "v0.4"
            code += """
import httpretty
from ddtrace.trace import tracer
from ddtrace.internal.telemetry import telemetry_writer

httpretty.enable(allow_net_connect=False)
httpretty.register_uri(httpretty.PUT, '%s/%s' % (tracer.agent_trace_url, 'v0.5/traces'))
httpretty.register_uri(httpretty.POST, '%s/%s' % (tracer.agent_trace_url, telemetry_writer._client._endpoint))
# profiler will collect snapshot during shutdown
httpretty.register_uri(httpretty.POST, '%s/%s' % (tracer.agent_trace_url, 'profiling/v1/input'))
"""

        if self.telemetry:
            code += "telemetry_writer.enable()\n"

        if self.tracing:
            code += "span = tracer.trace('test-x', service='bench-test'); span.finish()\n"

        if self.profiling:
            code += """
import time
import ddtrace.profiling.auto

start = time.perf_counter()
while time.perf_counter() - start < 1.0:
    pass  # Busy wait
"""

        # stage code for execution in a subprocess
        subp_cmd += [sys.executable, "-c", code]

        def _(loops):
            for _ in range(loops):
                subprocess.check_output(subp_cmd, env=env)

        yield _
