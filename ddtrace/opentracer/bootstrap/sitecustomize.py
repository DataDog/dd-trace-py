"""
Bootstrapping code that is run when using the `ddopentrace-run` Python entrypoint
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


try:
    from ddtrace.opentracer import Tracer, set_global_tracer
    from ddtrace.opentracer.settings import ConfigKeys

    enabled = os.environ.get("DATADOG_TRACE_ENABLED")
    hostname = os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME")
    port = os.environ.get("DATADOG_TRACE_AGENT_PORT")
    priority_sampling = os.environ.get("DATADOG_PRIORITY_SAMPLING")
    service_name = os.environ.get("DATADOG_SERVICE_NAME")
    debug = os.environ.get("DATADOG_TRACE_DEBUG")

    config = {}

    if enabled and enabled.lower() == "false":
        config[ConfigKeys.ENABLED] = False
    if enabled and enabled.lower() == "true":
        config[ConfigKeys.DEBUG] = True
    if hostname:
        config[ConfigKeys.AGENT_HOSTNAME] = hostname
    if port:
        config[ConfigKeys.AGENT_PORT] = int(port)
    if priority_sampling:
        config[ConfigKeys.PRIORITY_SAMPLING] = asbool(priority_sampling)

    # if 'DATADOG_ENV' in os.environ:
    #     tracer.set_tags({"env": os.environ["DATADOG_ENV"]})

    tracer = Tracer(service_name=service_name, config=config)
    set_global_tracer(tracer)

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
    log.warn("error configuring Datadog OpenTracing tracing", exc_info=True)
