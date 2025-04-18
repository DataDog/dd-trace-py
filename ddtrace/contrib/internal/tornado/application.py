from urllib.parse import urlparse

from tornado import template

import ddtrace
from ddtrace import config
from ddtrace.contrib.internal.tornado import decorators
from ddtrace.contrib.internal.tornado.constants import CONFIG_KEY
from ddtrace.contrib.internal.tornado.stack_context import context_provider
from ddtrace.internal.schema import schematize_service_name


def tracer_config(__init__, app, args, kwargs):
    """
    Wrap Tornado web application so that we can configure services info and
    tracing settings after the initialization.
    """
    # call the Application constructor
    __init__(*args, **kwargs)

    # default settings
    settings = {
        "tracer": ddtrace.tracer,
        "default_service": schematize_service_name(config._get_service("tornado-web")),
        "distributed_tracing": None,
        "analytics_enabled": None,
    }

    # update defaults with users settings
    user_settings = app.settings.get(CONFIG_KEY)
    if user_settings:
        settings.update(user_settings)

    app.settings[CONFIG_KEY] = settings
    tracer = settings["tracer"]
    service = settings["default_service"]

    # extract extra settings
    # TODO: Remove `FILTERS` from supported settings
    trace_processors = settings.get("settings", {}).get("FILTERS")

    tracer.configure(
        context_provider=context_provider,
        trace_processors=trace_processors,
    )
    tracer._wrap_executor = decorators.wrap_executor
    # TODO: Remove `enabled`, `hostname` and `port` settings in v4.0
    # Tracer should be configured via environment variables
    if settings.get("enabled") is not None:
        tracer.enabled = settings["enabled"]
    if settings.get("hostname") is not None or settings.get("port") is not None:
        curr_agent_url = urlparse(tracer._agent_url)
        hostname = settings.get("hostname", curr_agent_url.hostname)
        port = settings.get("port", curr_agent_url.port)
        tracer._agent_url = tracer._span_aggregator.writer.intake_url = f"{curr_agent_url.scheme}://{hostname}:{port}"
        tracer._recreate()
    # TODO: Remove tags from settings, tags should be set via `DD_TAGS` environment variable
    # set global tags if any
    tags = settings.get("tags", None)
    if tags:
        tracer.set_tags(tags)

    pin = ddtrace.trace.Pin(service=service)
    pin._tracer = tracer
    pin.onto(template)
