# Run this time file with the following configurations:
# Disable Instrumentation Telemetry -> time DD_REMOTE_CONFIGURATION_ENABLED=false DD_INSTRUMENTATION_TELEMETRY_ENABLED=false ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py
# Enable Instrumentation Telemetry (Python) -> time DD_REMOTE_CONFIGURATION_ENABLED=false  DD_INSTRUMENTATION_TELEMETRY_ENABLED=true ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py
# Enable Instrumentation Telemetry (Native) -> time DD_REMOTE_CONFIGURATION_ENABLED=false  DD_INSTRUMENTATION_TELEMETRY_ENABLED=false _DDTRACE_USE_LIBDD_TELEMETRY_WORKER=true ddtrace-run python ddtrace/internal/telemetry/libdd/bm_test.py

import httpretty

import ddtrace


# mock all network connections (this is done reduces noise in benchmarks)
httpretty.enable(allow_net_connect=False)
httpretty.register_uri(httpretty.PUT, "%s/%s" % (ddtrace.tracer.agent_trace_url, "v0.5/traces"))
httpretty.register_uri(
    httpretty.POST, "%s/%s" % (ddtrace.tracer.agent_trace_url, "telemetry/proxy/api/v2/apmtelemetry")
)

# patches the following integrations: Flask, Jinja
ddtrace.patch_all()
with ddtrace.tracer.trace("test-x", service="bench-test"):
    # generate and encode a Datadpg span
    # (note - endpoint is mocked and traces are not sent to an agent)
    pass
