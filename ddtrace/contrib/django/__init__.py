"""
The Django__ integration traces requests, views, template renderers, database
and cache calls in a Django application.


Enable Django tracing automatically via ``ddtrace-run``::

    ddtrace-run python manage.py runserver


Django tracing can also be enabled manually::

    from ddtrace import patch_all
    patch_all()


To have Django capture the tracer logs, ensure the ``LOGGING`` variable in
``settings.py`` looks similar to::

    LOGGING = {
        'loggers': {
            'ddtrace': {
                'handlers': ['console'],
                'level': 'WARNING',
            },
        },
    }


Configuration
~~~~~~~~~~~~~
.. py:data:: ddtrace.config.django['distributed_tracing_enabled']

   Whether or not to parse distributed tracing headers from requests received by your Django app.

   Default: ``True``

.. py:data:: ddtrace.config.django['analytics_enabled']

   Whether to analyze spans for Django in App Analytics.

   Can also be enabled with the ``DD_TRACE_DJANGO_ANALYTICS_ENABLED`` environment variable.

   Default: ``None``

.. py:data:: ddtrace.config.django['service_name']

   The service name reported for your Django app.

   Can also be configured via the ``DD_SERVICE_NAME`` environment variable.

   Default: ``'django'``

.. py:data:: ddtrace.config.django['cache_service_name']

   The service name reported for your Django app cache layer.

   Can also be configured via the ``DD_DJANGO_CACHE_SERVICE_NAME`` environment variable.

   Default: ``'django'``

.. py:data:: ddtrace.config.django['database_service_name_prefix']

   A string to be prepended to the service name reported for your Django app database layer.

   Can also be configured via the ``DD_DJANGO_DATABASE_SERVICE_NAME_PREFIX`` environment variable.

   The database service name is the name of the database appended with 'db'.

   Default: ``''``

.. py:data:: ddtrace.config.django['instrument_middleware']

   Whether or not to instrument middleware.

   Can also be enabled with the ``DD_DJANGO_INSTRUMENT_MIDDLEWARE`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['instrument_databases']

   Whether or not to instrument databases.

   Default: ``True``

.. py:data:: ddtrace.config.django['instrument_caches']

   Whether or not to instrument caches.

   Default: ``True``

.. py:data:: ddtrace.config.django['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``

.. py:data:: ddtrace.config.django['include_user_name']

   Whether or not to include the authenticated user's username as a tag on the root request span.

   Default: ``True``


Example::

    from ddtrace import config

    # Enable distributed tracing
    config.django['distributed_tracing_enabled'] = True

    # Override service name
    config.django['service_name'] = 'custom-service-name'


Migration from ddtrace<=0.33.0
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Django integration provides automatic migration from enabling tracing using
a middleware to the method consistent with our integrations. Application
developers are encouraged to convert their configuration of the tracer to the
latter.

1. Remove ``'ddtrace.contrib.django'`` from ``INSTALLED_APPS`` in
   ``settings.py``.

2. Replace ``DATADOG_TRACE`` configuration in ``settings.py`` according to the
   table below.

3. Remove ``TraceMiddleware`` or ``TraceExceptionMiddleware`` if used in
   ``settings.py``.

3. Enable Django tracing automatically via `ddtrace-run`` or manually by
   adding ``ddtrace.patch_all()`` to ``settings.py``.

The mapping from old configuration settings to new ones.

+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``DATADOG_TRACE``           | Configuration                                                                                                           |
+=============================+=========================================================================================================================+
| ``AGENT_HOSTNAME``          | ``DD_AGENT_HOST`` environment variable or ``tracer.configure(hostname=)``                                               |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``AGENT_PORT``              | ``DD_TRACE_AGENT_PORT`` environment variable or ``tracer.configure(port=)``                                             |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``AUTO_INSTRUMENT``         | N/A Instrumentation is automatic                                                                                        |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``INSTRUMENT_CACHE``        | ``config.django['instrument_caches']``                                                                                  |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``INSTRUMENT_DATABASE``     | ``config.django['instrument_databases']``                                                                               |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``INSTRUMENT_TEMPLATE``     | N/A Instrumentation is automatic                                                                                        |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``DEFAULT_DATABASE_PREFIX`` | ``config.django['database_service_name_prefix']``                                                                       |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``DEFAULT_SERVICE``         | ``DD_SERVICE_NAME`` environment variable or ``config.django['service_name']``                                           |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``DEFAULT_CACHE_SERVICE``   | ``config.django['cache_service_name']``                                                                                 |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``ENABLED``                 | ``tracer.configure(enabled=)``                                                                                          |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``DISTRIBUTED_TRACING``     | ``config.django['distributed_tracing_enabled']``                                                                        |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``ANALYTICS_ENABLED``       | ``config.django['analytics_enabled']``                                                                                  |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``ANALYTICS_SAMPLE_RATE``   | ``config.django['analytics_sample_rate']``                                                                              |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``TRACE_QUERY_STRING``      | ``config.django['trace_query_string']``                                                                                 |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``TAGS``                    | ``DD_TAGS`` environment variable or ``tracer.set_tags()``                                                               |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+
| ``TRACER``                  | N/A - if a particular tracer is required for the Django integration use ``Pin.override(Pin.get_from(django), tracer=)`` |
+-----------------------------+-------------------------------------------------------------------------------------------------------------------------+

Examples
--------
Before::

   # settings.py
   INSTALLED_APPS = [
       # your Django apps...
       'ddtrace.contrib.django',
   ]

   DATADOG_TRACE = {
       'AGENT_HOSTNAME': 'localhost',
       'AGENT_PORT': 8126,
       'AUTO_INSTRUMENT': True,
       'INSTRUMENT_CACHE': True,
       'INSTRUMENT_DATABASE': True,
       'INSTRUMENT_TEMPLATE': True,
       'DEFAULT_SERVICE': 'my-django-app',
       'DEFAULT_CACHE_SERVICE': 'my-cache',
       'DEFAULT_DATABASE_PREFIX': 'my-',
       'ENABLED': True,
       'DISTRIBUTED_TRACING': True,
       'ANALYTICS_ENABLED': True,
       'ANALYTICS_SAMPLE_RATE': 0.5,
       'TRACE_QUERY_STRING': None,
       'TAGS': {'env': 'production'},
       'TRACER': 'my.custom.tracer',
   }

After::

   # settings.py
   INSTALLED_APPS = [
       # your Django apps...
   ]

   from ddtrace import config, tracer
   tracer.configure(hostname='localhost', port=8126, enabled=True)
   config.django['service_name'] = 'my-django-app'
   config.django['cache_service_name'] = 'my-cache'
   config.django['database_service_name_prefix'] = 'my-'
   config.django['instrument_databases'] = True
   config.django['instrument_caches'] = True
   config.django['trace_query_string'] = True
   config.django['analytics_enabled'] = True
   config.django['analytics_sample_rate'] = 0.5
   tracer.set_tags({'env': 'production'})

   import my.custom.tracer
   from ddtrace import Pin, patch_all
   import django
   patch_all()
   Pin.override(Pin.get_from(django), tracer=my.custom.tracer)

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

.. __: https://www.djangoproject.com/
"""  # noqa: E501
from ...utils.importlib import require_modules


required_modules = ["django"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch", "TraceMiddleware"]


# define the Django app configuration
default_app_config = "ddtrace.contrib.django.apps.TracerConfig"
