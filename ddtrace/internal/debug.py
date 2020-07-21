import datetime
import logging
import os
import pkg_resources
import platform
import sys

import ddtrace
from ddtrace.internal import writer

from .logger import get_logger


logger = get_logger(__name__)


def ping_agent(api=None, hostname=None, port=None, uds_path=None):
    # Attempt to query the agent, returns an api.Response
    # or one of the following exceptions: httplib.HTTPException, OSError, IOError

    if not api:
        api = ddtrace.api.API(hostname=hostname, port=port, uds_path=uds_path,)

    # We can't use api.send_traces([]) since it'll shortcut
    # if traces is falsy.
    p = ddtrace.payload.Payload(encoder=api._encoder)

    # We can't use payload.add_trace([]) for the same reason
    # as api.send_trace([]).
    encoded = p.encoder.encode_trace([])
    p.traces.append(encoded)
    p.size += len(encoded)

    try:
        resp = api._flush(p)
    except Exception as e:
        resp = e

    return resp


def in_venv():
    # Works with both venv and virtualenv
    # https://stackoverflow.com/a/42580137
    return (
        "VIRTUAL_ENV" in os.environ
        or hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )


def tags_to_str(tags):
    # Turn a dict of tags to a string "k1:v1,k2:v2,..."
    return ",".join(["%s:%s" % (k, v) for k, v in tags.items()])


def collect(tracer):
    """Collect system and library information into a serializable dict.
    """

    # The tracer doesn't actually maintain a hostname/port, instead it stores
    # it on the possibly None writer which actually stores it on an API object.
    # Note that the tracer DOES have hostname and port attributes that it
    # sets to the defaults and ignores afterwards.
    if tracer.writer:
        if isinstance(tracer.writer, writer.LogWriter):
            agent_url = "AGENTLESS"
            hostname = port = uds_path = None
        else:
            hostname = tracer.writer.api.hostname
            port = tracer.writer.api.port
            uds_path = tracer.writer.api.uds_path
            https = tracer.writer.api.https

            # If all specified, uds_path will take precedence
            if uds_path:
                agent_url = "uds://%s" % uds_path
            else:
                proto = "https" if https else "http"
                agent_url = "%s://%s:%s" % (proto, hostname, port)
    else:
        # Else if we can't infer anything from the tracer, rely on the defaults.
        hostname = tracer.hostname
        port = tracer.port
        agent_url = "http://%s:%s" % (hostname, port)

    if (hostname and port) or uds_path:
        resp = ping_agent(hostname=hostname, port=port, uds_path=uds_path)
        if isinstance(resp, ddtrace.api.Response):
            if resp.status == 200:
                agent_error = None
            else:
                agent_error = "HTTP code %s, reason %s, message %s" % (resp.status, resp.reason, resp.msg)
        else:
            # There was an exception
            agent_error = "Agent not reachable. Exception raised: %s" % str(resp)
    else:
        # Serverless case
        agent_error = None

    is_venv = in_venv()

    packages_available = {p.project_name: p.version for p in pkg_resources.working_set}
    integration_configs = {}
    for module, enabled in ddtrace.monkey.PATCH_MODULES.items():
        # TODO: this check doesn't work in all cases... we need a mapping
        #       between the module and the library name.
        module_available = module in packages_available
        module_instrumented = module in ddtrace.monkey._PATCHED_MODULES
        module_imported = module in sys.modules

        if enabled:
            # Note that integration configs aren't added until the integration
            # module is imported. This typically occurs as a side-effect of
            # patch().
            # This also doesn't load work in all cases since we don't always
            # name the configuration entry the same as the integration module
            # name :/
            config = ddtrace.config._config.get(module, "N/A")
        else:
            config = None

        if module_available:
            integration_configs[module] = dict(
                enabled=enabled,
                instrumented=module_instrumented,
                module_available=module_available,
                module_version=packages_available[module],
                module_imported=module_imported,
                config=config,
            )
        else:
            # Use N/A here to avoid the additional clutter of an entire
            # config dictionary for a module that isn't available.
            integration_configs[module] = "N/A"

    pip_version = packages_available.get("pip", "N/A")

    return dict(
        # Timestamp UTC ISO 8601
        date=datetime.datetime.utcnow().isoformat(),
        # eg. "Linux", "Darwin"
        os_name=platform.system(),
        # eg. 12.5.0
        os_version=platform.release(),
        is_64_bit=sys.maxsize > 2 ** 32,
        architecture=platform.architecture()[0],
        vm=platform.python_implementation(),
        version=ddtrace.__version__,
        lang="python",
        lang_version=platform.python_version(),
        pip_version=pip_version,
        in_virtual_env=is_venv,
        agent_url=agent_url,
        agent_error=agent_error,
        env=ddtrace.config.env or "",
        is_global_tracer=tracer == ddtrace.tracer,
        enabled_env_setting=os.getenv("DATADOG_TRACE_ENABLED"),
        tracer_enabled=tracer.enabled,
        sampler_type=type(tracer.sampler).__name__ if tracer.sampler else "N/A",
        priority_sampler_type=type(tracer.priority_sampler).__name__ if tracer.priority_sampler else "N/A",
        service=ddtrace.config.service or "",
        debug=ddtrace.tracer.log.isEnabledFor(logging.DEBUG),
        enabled_cli="ddtrace" in os.getenv("PYTHONPATH", ""),
        analytics_enabled=ddtrace.config.analytics_enabled,
        log_injection_enabled=ddtrace.config.logs_injection,
        health_metrics_enabled=ddtrace.config.health_metrics_enabled,
        dd_version=ddtrace.config.version or "",
        priority_sampling_enabled=tracer.priority_sampler is not None,
        global_tags=os.getenv("DD_TAGS", ""),
        tracer_tags=tags_to_str(tracer.tags),
        integrations=integration_configs,
    )
