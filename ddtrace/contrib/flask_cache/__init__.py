"""
The flask cache tracer will track any access to a cache backend.
You can use this tracer together with the Flask tracer middleware.

The tracer supports both `Flask-Cache <https://pythonhosted.org/Flask-Cache/>`_
and `Flask-Caching <https://flask-caching.readthedocs.io/>`_.

To install the tracer, ``from ddtrace import tracer`` needs to be added::

    from ddtrace import tracer
    from ddtrace.contrib.flask_cache import get_traced_cache

and the tracer needs to be initialized::

    Cache = get_traced_cache(tracer, service='my-flask-cache-app')

Here is the end result, in a sample app::

    from flask import Flask

    from ddtrace import tracer
    from ddtrace.contrib.flask_cache import get_traced_cache

    app = Flask(__name__)

    # get the traced Cache class
    Cache = get_traced_cache(tracer, service='my-flask-cache-app')

    # use the Cache as usual with your preferred CACHE_TYPE
    cache = Cache(app, config={'CACHE_TYPE': 'simple'})

    def counter():
        # this access is traced
        conn_counter = cache.get("conn_counter")

Use a specific ``Cache`` implementation with::

    from ddtrace import tracer
    from ddtrace.contrib.flask_cache import get_traced_cache

    from flask_caching import Cache

    Cache = get_traced_cache(tracer, service='my-flask-cache-app', cache_cls=Cache)

"""


from ddtrace.contrib.internal.flask_cache.patch import get_traced_cache
from ddtrace.contrib.internal.flask_cache.patch import get_version  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("get_version",):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["get_traced_cache"]
