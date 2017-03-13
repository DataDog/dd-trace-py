from . import handlers, decorators
from .settings import CONFIG_KEY
from .stack_context import TracerStackContext

from ...ext import AppTypes


def trace_app(app, tracer, service='tornado-web'):
    """
    Tracing function that patches the Tornado web application so that it will be
    traced using the given ``tracer``.
    """
    # safe-guard: don't trace an application twice
    if getattr(app, '__datadog_trace', False):
        return
    setattr(app, '__datadog_trace', True)

    # configure Datadog settings
    app.settings[CONFIG_KEY] = {
        'tracer': tracer,
        'service': service,
    }

    # the tracer must use the right Context propagation and wrap executor
    tracer.configure(
        context_provider=TracerStackContext.current_context,
        wrap_executor=decorators.wrap_executor,
    )

    # configure the current service
    tracer.set_service_info(
        service=service,
        app='tornado',
        app_type=AppTypes.web,
    )

    # wrap all Application handlers to collect tracing information
    for _, specs in app.handlers:
        for spec in specs:
            handlers.wrap_methods(spec.handler_class)

    # wrap a custom default handler class if defined via settings
    default_handler_class = app.settings.get('default_handler_class')
    if default_handler_class:
        handlers.wrap_methods(default_handler_class)
        return

    # if a default_handler_class is not defined, it means that the default ErrorHandler is used;
    # to avoid a monkey-patch in the Tornado code, we use a custom TracerErrorHandler that behaves
    # exactly like the default one, but it's wrapped as the others
    app.settings['default_handler_class'] = handlers.TracerErrorHandler
    app.settings['default_handler_args'] = dict(status_code=404)


def untrace_app(app):
    """
    Remove all tracing functions in a Tornado web application.
    """
    # if the application is not traced there is nothing to do
    if not getattr(app, '__datadog_trace', False):
        return
    delattr(app, '__datadog_trace')

    # remove wrappers from all handlers
    for _, specs in app.handlers:
        for spec in specs:
            handlers.unwrap_methods(spec.handler_class)

    default_handler_class = app.settings.get('default_handler_class')

    # remove the default handler class if it's our TracerErrorHandler
    if default_handler_class is handlers.TracerErrorHandler:
        app.settings.pop('default_handler_class')
        app.settings.pop('default_handler_args')
        return

    # unset the default_handler_class tracing otherwise
    if default_handler_class:
        handlers.unwrap_methods(default_handler_class)
