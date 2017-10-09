import ddtrace

from tornado import template

from . import decorators, context_provider
from .constants import CONFIG_KEY

from ...ext import AppTypes


def tracer_config(__init__, app, args, kwargs):
    """
    Wrap Tornado web application so that we can configure services info and
    tracing settings after the initialization.
    """
    # call the Application constructor
    __init__(*args, **kwargs)

    # default settings
    settings = {
        'tracer': ddtrace.tracer,
        'default_service': 'tornado-web',
    }

    # update defaults with users settings
    user_settings = app.settings.get(CONFIG_KEY)
    if user_settings:
        settings.update(user_settings)

    app.settings[CONFIG_KEY] = settings
    tracer = settings['tracer']
    service = settings['default_service']

    # the tracer must use the right Context propagation and wrap executor;
    # this action is done twice because the patch() method uses the
    # global tracer while here we can have a different instance (even if
    # this is not usual).
    tracer.configure(
        context_provider=context_provider,
        wrap_executor=decorators.wrap_executor,
        enabled=settings.get('enabled', None),
        hostname=settings.get('agent_hostname', None),
        port=settings.get('agent_port', None),
    )

    # set global tags if any
    tags = settings.get('tags', None)
    if tags:
        tracer.set_tags(tags)

    # configure the current service
    tracer.set_service_info(
        service=service,
        app='tornado',
        app_type=AppTypes.web,
    )

    # configure the PIN object for template rendering
    ddtrace.Pin(app='tornado', service=service, app_type='web', tracer=tracer).onto(template)
