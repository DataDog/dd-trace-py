"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""
import logging
import os
import imp
import sys

from ddtrace.utils.formats import asbool, get_env, parse_tags_str
from ddtrace.internal.logger import get_logger
from ddtrace import config, constants
from ddtrace.tracer import debug_mode, DD_LOG_FORMAT


if config.logs_injection:
    # immediately patch logging if trace id injected
    from ddtrace import patch

    patch(logging=True)


# DEV: Once basicConfig is called here, future calls to it cannot be used to
# change the formatter since it applies the formatter to the root handler only
# upon initializing it the first time.
# See https://github.com/python/cpython/blob/112e4afd582515fcdcc0cde5012a4866e5cfda12/Lib/logging/__init__.py#L1550
# Debug mode from the tracer will do a basicConfig so only need to do this otherwise
if not debug_mode:
    if config.logs_injection:
        logging.basicConfig(format=DD_LOG_FORMAT)
    else:
        logging.basicConfig()

log = get_logger(__name__)

EXTRA_PATCHED_MODULES = {
    "bottle": True,
    "django": True,
    "falcon": True,
    "flask": True,
    "pylons": True,
    "pyramid": True,
}


def update_patched_modules():
    modules_to_patch = os.environ.get("DATADOG_PATCH_MODULES")
    if not modules_to_patch:
        return

    modules = parse_tags_str(modules_to_patch)
    for module, should_patch in modules.items():
        EXTRA_PATCHED_MODULES[module] = asbool(should_patch)


try:
    from ddtrace import tracer

    # Respect DATADOG_* environment variables in global tracer configuration
    # TODO: these variables are deprecated; use utils method and update our documentation
    # correct prefix should be DD_*
    hostname = os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME"))
    port = os.environ.get("DATADOG_TRACE_AGENT_PORT")
    priority_sampling = os.environ.get("DATADOG_PRIORITY_SAMPLING")
    profiling = asbool(os.environ.get("DD_PROFILING_ENABLED", False))

    if profiling:
        import ddtrace.profiling.auto  # noqa: F401

    opts = {}

    if asbool(os.environ.get("DATADOG_TRACE_ENABLED", True)):
        patch = True
    else:
        patch = False
        opts["enabled"] = False

    if hostname:
        opts["hostname"] = hostname
    if port:
        opts["port"] = int(port)
    if priority_sampling:
        opts["priority_sampling"] = asbool(priority_sampling)

    opts["collect_metrics"] = asbool(get_env("runtime_metrics", "enabled"))

    if opts:
        tracer.configure(**opts)

    if patch:
        update_patched_modules()
        from ddtrace import patch_all

        patch_all(**EXTRA_PATCHED_MODULES)

    if "DATADOG_ENV" in os.environ:
        tracer.set_tags({constants.ENV_KEY: os.environ["DATADOG_ENV"]})

    if "DD_TRACE_GLOBAL_TAGS" in os.environ:
        env_tags = os.getenv("DD_TRACE_GLOBAL_TAGS")
        tracer.set_tags(parse_tags_str(env_tags))

    # Ensure sitecustomize.py is properly called if available in application directories:
    # * exclude `bootstrap_dir` from the search
    # * find a user `sitecustomize.py` module
    # * import that module via `imp`
    bootstrap_dir = os.path.dirname(__file__)
    path = list(sys.path)

    if bootstrap_dir in path:
        path.remove(bootstrap_dir)

    try:
        (f, path, description) = imp.find_module("sitecustomize", path)
    except ImportError:
        pass
    else:
        # `sitecustomize.py` found, load it
        log.debug("sitecustomize from user found in: %s", path)
        imp.load_module("sitecustomize", f, path, description)

    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True
except Exception:
    loaded = False
    log.warning("error configuring Datadog tracing", exc_info=True)
