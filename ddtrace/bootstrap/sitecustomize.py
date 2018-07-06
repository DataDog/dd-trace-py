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


def instrument_opentracing():
    try:
        import opentracing
        from ddtrace.opentracer import Tracer, set_global_tracer
        from ddtrace.opentracer.settings import ConfigKeys

        # Respect DATADOG_* environment variables in global tracer configuration
        # TODO: these variables are deprecated; use utils method and update our documentation
        # correct prefix should be DD_*
        enabled = os.environ.get("DATADOG_TRACE_ENABLED")
        hostname = os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME")
        port = os.environ.get("DATADOG_TRACE_AGENT_PORT")
        priority_sampling = os.environ.get("DATADOG_PRIORITY_SAMPLING")
        service_name = os.environ.get("DATADOG_SERVICE_NAME")
        debug = os.environ.get("DATADOG_TRACE_DEBUG")

        config = {}

        if enabled and enabled.lower() == "false":
            config[ConfigKeys.ENABLED] = False
        if debug and debug.lower() == "true":
            config[ConfigKeys.DEBUG] = True
        if hostname:
            config[ConfigKeys.AGENT_HOSTNAME] = hostname
        if port:
            config[ConfigKeys.AGENT_PORT] = int(port)
        if priority_sampling:
            config[ConfigKeys.PRIORITY_SAMPLING] = asbool(priority_sampling)

        tracer = Tracer(service_name=service_name, config=config)
        set_global_tracer(tracer)

        # TODO: there currently is no patching support for OpenTracing
        # once there is it should go here
        return True
    except Exception:
        log.debug("opentracing patching failed", exc_info=True)
        return False


def instrument_datadog():
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

    except Exception as e:
        log.warn("error configuring Datadog tracing", exc_info=True)


def instrument():
    try:
        # first try to patch if OpenTracing is installed
        if not instrument_opentracing():
            # if this fails then do our usual patching
            instrument_datadog()

        # Ensure sitecustomize.py is properly called if available in application directories:
        # * exclude `bootstrap_dir` from the search
        # * find a user `sitecustomize.py` module
        # * import that module via `imp`
        bootstrap_dir = os.path.dirname(__file__)
        path = list(sys.path)
        path.remove(bootstrap_dir)

        try:
            (f, path, description) = imp.find_module('sitecustomize', path)
        except ImportError:
            pass
        else:
            # `sitecustomize.py` found, load it
            log.debug('sitecustomize from user found in: %s', path)
            imp.load_module('sitecustomize', f, path, description)

    except Exception as e:
        log.warn("error configuring Datadog tracing", exc_info=True)


instrument()
