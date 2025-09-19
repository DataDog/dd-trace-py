"""
The Django__ integration traces requests, views, template renderers, database
and cache calls in a Django application.


Enable Django tracing automatically via ``ddtrace-run``::

    ddtrace-run python manage.py runserver


Django tracing can also be enabled manually::

    import ddtrace.auto


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
    will not work with ``ddtrace-run`` and before a call to ``patch`` or ``import ddtrace.auto``.


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

   Can also be configured via the ``DD_DJANGO_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``

.. envvar:: DD_DJANGO_TRACING_MINIMAL

   Enables minimal tracing mode for performance-sensitive applications. When enabled, this disables
   Django ORM, cache, and template instrumentation while keeping middleware instrumentation enabled.
   This can significantly reduce overhead by removing Django-specific spans while preserving visibility
   into the underlying database drivers, cache clients, and other integrations.

   This is equivalent to setting:
   - ``DD_DJANGO_INSTRUMENT_TEMPLATES=false``
   - ``DD_DJANGO_INSTRUMENT_DATABASES=false``
   - ``DD_DJANGO_INSTRUMENT_CACHES=false``

   For example, with ``DD_DJANGO_INSTRUMENT_DATABASES=false``, Django ORM query spans are disabled
   but database driver spans (e.g., psycopg, MySQLdb) will still be created, providing visibility
   into the actual database queries without the Django ORM overhead.

   Consider using this option if your application is performance-sensitive and the additional
   Django-layer spans are not required for your observability needs.

   Default: ``False``

   *New in version v3.15.0.*

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

.. py:data:: ddtrace.config.django['always_create_database_spans']

   Whether or not to enforce that a Django database span is created regardless of other
   database instrumentation.

   Enabling this will provide database spans when the database engine is not yet supported
   by ``ddtrace``, however it may result in duplicate database spans when the database
   engine is supported and enabled.

   Can also be enabled with the ``DD_DJANGO_ALWAYS_CREATE_DATABASE_SPANS`` environment variable.

   Default: ``True``

   *New in version v3.13.0.*

.. py:data:: ddtrace.config.django['instrument_caches']

   Whether or not to instrument caches.

    Can also be enabled with the ``DD_DJANGO_INSTRUMENT_CACHES`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django.http['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``

.. py:data:: ddtrace.config.django['include_user_name']

   Whether or not to include the authenticated user's name/id as a tag on the root request span.

   Can also be configured via the ``DD_DJANGO_INCLUDE_USER_NAME`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['include_user_email']

   (ASM) Whether or not to include the authenticated user's email (if available) as a tag on the root request span on a user event.

   Can also be configured via the ``DD_DJANGO_INCLUDE_USER_EMAIL`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.django['include_user_login']

   (ASM) Whether or not to include the authenticated user's login (if available) as a tag on the root request span on a user event.

   Can also be configured via the ``DD_DJANGO_INCLUDE_USER_LOGIN`` environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.django['include_user_realname']

   (ASM) Whether or not to include the authenticated user's real name (if available) as a tag on the root request span on a user event.

   Can also be configured via the ``DD_DJANGO_INCLUDE_USER_REALNAME`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.django['use_handler_resource_format']

   Whether or not to use the resource format `"{method} {handler}"`. Can also be
   enabled with the ``DD_DJANGO_USE_HANDLER_RESOURCE_FORMAT`` environment
   variable.

   The default resource format for Django >= 2.2.0 is otherwise `"{method} {urlpattern}"`.

   Default: ``False``

.. py:data:: ddtrace.config.django['use_handler_with_url_name_resource_format']

   Whether or not to use the resource format `"{method} {handler}.{url_name}"`. Can also be
   enabled with the ``DD_DJANGO_USE_HANDLER_WITH_URL_NAME_RESOURCE_FORMAT`` environment
   variable.

   This configuration applies only for Django <= 2.2.0.

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
