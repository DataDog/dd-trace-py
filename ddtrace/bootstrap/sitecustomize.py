"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

import os
import imp
import sys
import logging

from ddtrace.utils.formats import asbool


debug = os.environ.get("DATADOG_TRACE_DEBUG")
if debug and debug.lower() == "true":
    logging.basicConfig(level=logging.DEBUG)
else:
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


def update_patched_modules():
    for patch in os.environ.get("DATADOG_PATCH_MODULES", '').split(','):
        if len(patch.split(':')) != 2:
            log.debug("skipping malformed patch instruction")
            continue

        module, should_patch = patch.split(':')
        if should_patch.lower() not in ['true', 'false']:
            log.debug("skipping malformed patch instruction for %s", module)
            continue

        EXTRA_PATCHED_MODULES.update({module: should_patch.lower() == 'true'})


try:
    from ddtrace import tracer
    patch = True

    # Respect DATADOG_* environment variables in global tracer configuration
    # TODO: these variables are deprecated; use utils method and update our documentation
    # correct prefix should be DD_*
    enabled = os.environ.get("DATADOG_TRACE_ENABLED")
    hostname = os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME")
    port = os.environ.get("DATADOG_TRACE_AGENT_PORT")
    priority_sampling = os.environ.get("DATADOG_PRIORITY_SAMPLING")

    opts = {}

    if enabled and enabled.lower() == "false":
        opts["enabled"] = False
        patch = False
    if hostname:
        opts["hostname"] = hostname
    if port:
        opts["port"] = int(port)
    if priority_sampling:
        opts["priority_sampling"] = asbool(priority_sampling)

    if opts:
        tracer.configure(**opts)

    if patch:
        update_patched_modules()
        from ddtrace import patch_all; patch_all(**EXTRA_PATCHED_MODULES) # noqa

    debug = os.environ.get("DATADOG_TRACE_DEBUG")
    if debug and debug.lower() == "true":
        tracer.debug_logging = True

    if 'DATADOG_ENV' in os.environ:
        tracer.set_tags({"env": os.environ["DATADOG_ENV"]})

    # Ensure sitecustomize.py is properly called if available in application directories:
    # * exclude `bootstrap_dir` from the search
    # * find a user `sitecustomize.py` module
    # * import that module via `imp`
    bootstrap_dir = os.path.dirname(__file__)
    path = list(sys.path)

    if bootstrap_dir in path:
        path.remove(bootstrap_dir)

    try:
        (f, path, description) = imp.find_module('sitecustomize', path)
    except ImportError:
        pass
    else:
        # `sitecustomize.py` found, load it
        log.debug('sitecustomize from user found in: %s', path)
        imp.load_module('sitecustomize', f, path, description)

    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True
except Exception as e:
    loaded = False
    log.warn("error configuring Datadog tracing", exc_info=True)
