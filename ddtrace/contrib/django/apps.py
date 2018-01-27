import logging

# 3rd party
from django.apps import AppConfig, apps

# project
from .db import patch_db
from .conf import settings
from .cache import patch_cache
from .templates import patch_template
from .middleware import insert_exception_middleware, insert_trace_middleware

from ...ext import AppTypes


log = logging.getLogger(__name__)


class TracerConfig(AppConfig):
    name = 'ddtrace.contrib.django'
    label = 'datadog_django'

    def ready(self):
        """
        Ready is called as soon as the registry is fully populated.
        Tracing capabilities must be enabled in this function so that
        all Django internals are properly configured.
        """
        tracer = settings.TRACER

        if settings.TAGS:
            tracer.set_tags(settings.TAGS)

        # configure the tracer instance
        # TODO[manu]: we may use configure() but because it creates a new
        # AgentWriter, it breaks all tests. The configure() behavior must
        # be changed to use it in this integration
        tracer.enabled = settings.ENABLED
        tracer.writer.api.hostname = settings.AGENT_HOSTNAME
        tracer.writer.api.port = settings.AGENT_PORT

        # define the service details
        tracer.set_service_info(
            app='django',
            app_type=AppTypes.web,
            service=settings.DEFAULT_SERVICE,
        )

        if settings.AUTO_INSTRUMENT:
            # trace Django internals
            insert_trace_middleware()
            insert_exception_middleware()

            if settings.INSTRUMENT_TEMPLATE:
                try:
                    patch_template(tracer)
                except Exception:
                    log.exception('error patching Django template rendering')

            if settings.INSTRUMENT_DATABASE:
                try:
                    patch_db(tracer)
                except Exception:
                    log.exception('error patching Django database connections')

            if settings.INSTRUMENT_CACHE:
                try:
                    patch_cache(tracer)
                except Exception:
                    log.exception('error patching Django cache')

            # Instrument rest_framework app to trace custom exception handling.
            if apps.is_installed('rest_framework'):
                try:
                    from .restframework import patch_restframework
                    patch_restframework(tracer)
                except Exception:
                    log.exception('error patching rest_framework app')
