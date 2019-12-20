"""
The Django__ integration traces requests, views, template renderers, database
and cache calls in a Django application.


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

   Whether to generate APM events for Django in Trace Search & Analytics.

   Can also be enabled with the ``DD_DJANGO_ANALYTICS_ENABLED`` environment variable.

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

.. py:data:: ddtrace.config.django['instrument_databases']

   Whether or not to instrument databases.

   Default: ``True``

.. py:data:: ddtrace.config.django['instrument_caches']

   Whether or not to instrument caches.

   Default: ``True``

.. py:data:: ddtrace.config.django['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``


Example::

    from ddtrace import config

    # Enable distributed tracing
    config.django['distributed_tracing_enabled'] = True

    # Override service name
    config.django['service_name'] = 'custom-service-name'


.. __: https://www.djangoproject.com/
"""
from ...utils.importlib import require_modules


required_modules = ["django"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]


# define the Django app configuration
default_app_config = "ddtrace.contrib.django.apps.TracerConfig"
