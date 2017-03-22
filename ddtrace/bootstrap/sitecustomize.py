"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

import os
import logging

logging.basicConfig()
log = logging.getLogger(__name__)

EXTRA_PATCHED_MODULES = {
    "bottle": True,
    "django": True,
    "falcon": True,
    "flask": True,
    "pylons": True,
    "pyramid": True,
}

try:
    from ddtrace import tracer
    patch = True

    # Respect DATADOG_* environment variables in global tracer configuration
    enabled = os.environ.get("DATADOG_TRACE_ENABLED")
    hostname = os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME")
    port = os.environ.get("DATADOG_TRACE_AGENT_PORT")
    opts = {}

    if enabled and enabled.lower() == "false":
        opts["enabled"] = False
        patch = False
    if hostname:
        opts["hostname"] = hostname
    if port:
        opts["port"] = int(port)

    if opts:
        tracer.configure(**opts)

    if patch:
        from ddtrace import patch_all; patch_all(**EXTRA_PATCHED_MODULES) # noqa

    debug = os.environ.get("DATADOG_TRACE_DEBUG")
    if debug and debug.lower() == "true":
        tracer.debug_logging = True

    if 'DATADOG_ENV' in os.environ:
        tracer.set_tags({"env": os.environ["DATADOG_ENV"]})
except Exception as e:
    log.warn("error configuring Datadog tracing", exc_info=True)
