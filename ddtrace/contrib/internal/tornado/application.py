from urllib.parse import urlparse

from tornado import template
from tornado.routing import PathMatches
import tornado.web

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.tornado import decorators
from ddtrace.contrib.internal.tornado.constants import CONFIG_KEY
from ddtrace.contrib.internal.tornado.handlers import _path_for_path_match
from ddtrace.contrib.internal.tornado.stack_context import context_provider
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.schema import schematize_service_name
from ddtrace.trace import tracer


_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


def _handler_has_method(handler_cls, method):
    """Check if a RequestHandler subclass has overridden an HTTP method."""
    for cls in handler_cls.__mro__:
        if cls is tornado.web.RequestHandler:
            return False
        if method in cls.__dict__:
            return True
    return False


def _collect_endpoints(app):
    """Walk through the application's routing rules and register endpoints."""
    rules = list(getattr(getattr(app, "default_router", None), "rules", []))

    while rules:
        rule = rules.pop()
        matcher = getattr(rule, "matcher", None)
        path = _path_for_path_match(matcher) if isinstance(matcher, PathMatches) else None
        target = getattr(rule, "target", None)

        if path is not None and isinstance(target, type) and issubclass(target, tornado.web.RequestHandler):
            resource_name = "{}.{}".format(target.__module__, target.__name__)
            for method_name in _HTTP_METHODS:
                if _handler_has_method(target, method_name):
                    endpoint_collection.add_endpoint(
                        method_name.upper(),
                        path,
                        resource_name=resource_name,
                        operation_name="tornado.request",
                    )

        if target is not None and hasattr(target, "rules"):
            rules.extend(target.rules)


def tracer_config(__init__, app, args, kwargs):
    """
    Wrap Tornado web application so that we can configure services info and
    tracing settings after the initialization.
    """
    # call the Application constructor
    __init__(*args, **kwargs)

    # default settings
    settings = {
        "default_service": schematize_service_name(config._get_service("tornado-web")),
        "distributed_tracing": None,
    }

    # update defaults with users settings
    user_settings = app.settings.get(CONFIG_KEY)
    if user_settings:
        settings.update(user_settings)

    app.settings[CONFIG_KEY] = settings
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

    pin = Pin(service=service)
    pin.onto(template)

    _collect_endpoints(app)
