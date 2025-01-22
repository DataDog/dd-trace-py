r"""To trace requests from a Pyramid application, trace your application
config::


    from pyramid.config import Configurator
    from ddtrace.contrib.pyramid import trace_pyramid

    settings = {
        'datadog_trace_service' : 'my-web-app-name',
    }

    config = Configurator(settings=settings)
    trace_pyramid(config)

    # use your config as normal.
    config.add_route('index', '/')

Available settings are:

* ``datadog_trace_service``: change the `pyramid` service name
* ``datadog_trace_enabled``: sets if the Tracer is enabled or not
* ``datadog_distributed_tracing``: set it to ``False`` to disable Distributed Tracing

If you use the ``pyramid.tweens`` settings value to set the tweens for your
application, you need to add ``ddtrace.contrib.pyramid:trace_tween_factory``
explicitly to the list. For example::

    settings = {
        'datadog_trace_service' : 'my-web-app-name',
        'pyramid.tweens', 'your_tween_no_1\\nyour_tween_no_2\\nddtrace.contrib.pyramid:trace_tween_factory',
    }

    config = Configurator(settings=settings)
    trace_pyramid(config)

    # use your config as normal.
    config.add_route('index', '/')

:ref:`All HTTP tags <http-tagging>` are supported for this integration.

"""


# Required to allow users to import from  `ddtrace.contrib.pyramid.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.pyramid.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.pyramid.patch import patch  # noqa: F401
from ddtrace.contrib.internal.pyramid.trace import includeme
from ddtrace.contrib.internal.pyramid.trace import trace_pyramid
from ddtrace.contrib.internal.pyramid.trace import trace_tween_factory
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("patch", "get_version"):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["trace_pyramid", "trace_tween_factory", "includeme"]
