import ddtrace

from . import decorators
from .constants import CONFIG_KEY
from .stack_context import TracerStackContext

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
        'service': 'tornado-web',
    }

    # update defaults with users settings
    user_settings = app.settings.get(CONFIG_KEY)
    if user_settings:
        settings.update(user_settings)

    app.settings[CONFIG_KEY] = settings
    tracer = settings['tracer']
    service = settings['service']

    # the tracer must use the right Context propagation and wrap executor;
    # this action is done twice because the patch() method uses the
    # global tracer while here we can have a different instance (even if
    # this is not usual).
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
