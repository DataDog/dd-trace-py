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

.. important::

    Note that the in-code configuration must be run before Django is instrumented. This means that in-code configuration
    will not work with ``ddtrace-run`` and before a call to ``patch`` or ``patch_all``.


.. py:data:: ddtrace.config.django['distributed_tracing_enabled']

   Whether or not to parse distributed tracing headers from requests received by your Django app.

   Default: ``True``

.. py:data:: ddtrace.config.django['service_name']

   The service name reported for your Django app.

   Can also be configured via the ``DD_SERVICE`` environment variable.

   Default: ``'django'``

.. py:data:: ddtrace.config.django['cache_service_name']

   The service name reported for your Django app cache layer.

   Can also be configured via the ``DD_DJANGO_CACHE_SERVICE_NAME`` environment variable.

   Default: ``'django'``

.. py:data:: ddtrace.config.django['database_service_name']

   A string reported as the service name of the Django app database layer.

   Can also be configured via the ``DD_DJANGO_DATABASE_SERVICE_NAME`` environment variable.

   Takes precedence over database_service_name_prefix.

   Default: ``''``

.. py:data:: ddtrace.config.django['database_service_name_prefix']

   A string to be prepended to the service name reported for your Django app database layer.

   Can also be configured via the ``DD_DJANGO_DATABASE_SERVICE_NAME_PREFIX`` environment variable.

   The database service name is the name of the database appended with 'db'. Has a lower precedence than database_service_name.

   Default: ``''``

.. py:data:: ddtrace.config.django["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_DJANGO_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.django['instrument_middleware']

   Whether or not to instrument middleware.

   Can also be enabled with the ``DD_DJANGO_INSTRUMENT_MIDDLEWARE`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['instrument_templates']

   Whether or not to instrument template rendering.

   Can also be enabled with the ``DD_DJANGO_INSTRUMENT_TEMPLATES`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['instrument_databases']

   Whether or not to instrument databases.

   Can also be enabled with the ``DD_DJANGO_INSTRUMENT_DATABASES`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['instrument_caches']

   Whether or not to instrument caches.

    Can also be enabled with the ``DD_DJANGO_INSTRUMENT_CACHES`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``

.. py:data:: ddtrace.config.django['include_user_name']

   Whether or not to include the authenticated user's username as a tag on the root request span.

   Default: ``True``

.. py:data:: ddtrace.config.django['use_handler_resource_format']

   Whether or not to use the resource format `"{method} {handler}"`. Can also be
   enabled with the ``DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT`` environment
   variable.

   The default resource format for Django >= 2.2.0 is otherwise `"{method} {urlpattern}"`.

   Default: ``False``

.. py:data:: ddtrace.config.django['use_legacy_resource_format']

   Whether or not to use the legacy resource format `"{handler}"`. Can also be
   enabled with the ``DD_DJANGO_USE_LEGACY_RESOURCE_FORMAT`` environment
   variable.

   The default resource format for Django >= 2.2.0 is otherwise `"{method} {urlpattern}"`.

   Default: ``False``

Example::

    from ddtrace import config

    # Enable distributed tracing
    config.django['distributed_tracing_enabled'] = True

    # Override service name
    config.django['service_name'] = 'custom-service-name'


:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

.. __: https://www.djangoproject.com/
"""  # noqa: E501
from ...internal.utils.importlib import require_modules


required_modules = ["django"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from . import patch as _patch
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "_patch"]
