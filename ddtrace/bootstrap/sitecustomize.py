"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

import os
import logging

logging.basicConfig()
log = logging.getLogger(__name__)

try:
    from ddtrace import tracer

    # Respect DATADOG_* environment variables in global tracer configuration
    enabled = os.environ.get("DATADOG_TRACE_ENABLED")
    if enabled and enabled.lower() == "false":
        tracer.configure(enabled=False)
    else:
        from ddtrace import patch_all; patch_all(django=True, flask=True, pylons=True) # noqa

    debug = os.environ.get("DATADOG_TRACE_DEBUG")
    if debug and debug.lower() == "true":
        tracer.debug_logging = True

    if 'DATADOG_ENV' in os.environ:
        tracer.set_tags({"env": os.environ["DATADOG_ENV"]})
except Exception as e:
    log.warn("error configuring Datadog tracing", exc_info=True)
