# Run this time file with the following configurations:
# time python ""
# time ddtrace-run python ""
# Disable Instrumentation Telemetry -> time DD_INSTRUMENTATION_TELEMETRY_ENABLED=false ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py
# Enable Instrumentation Telemetry (Python) -> time ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py
# Enable Instrumentation Telemetry (Native) -> time _DDTRACE_USE_LIBDD_TELEMETRY_WORKER=true ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py
# Enable Instrumentation Telemetry (Native + debug logs) -> time _DDTRACE_USE_LIBDD_TELEMETRY_WORKER=true DD_TELEMETRY_DEBUG=true ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py

import httpretty

import ddtrace


# mock all network connections (this is done reduces noise in benchmarks)
httpretty.enable(allow_net_connect=False)
# mock trace agent
httpretty.register_uri(httpretty.PUT, "%s/%s" % (ddtrace.tracer.agent_trace_url, "v0.5/traces"))
# mock instrumentation telemetry endpoint
httpretty.register_uri(
    httpretty.POST, "%s/%s" % (ddtrace.tracer.agent_trace_url, "telemetry/proxy/api/v2/apmtelemetry")
)
# Mock remote config
httpretty.register_uri(httpretty.POST, "%s/%s" % (ddtrace.tracer.agent_trace_url, "v0.7/config"))
httpretty.register_uri(
    httpretty.GET,
    "%s/%s" % (ddtrace.tracer.agent_trace_url, "info"),
    body='{"endpoints": "v0.7/config"}',
)

# patches the following integrations: Flask, Jinja
ddtrace.patch_all()
with ddtrace.tracer.trace("test-x", service="bench-test"):
    # generate and encode a Datadpg span
    # (note - endpoint is mocked and traces are not sent to an agent)
    pass
