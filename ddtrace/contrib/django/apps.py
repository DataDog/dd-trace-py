from django.apps import AppConfig, apps
from django.db import connections

from .db import patch_db
from .conf import settings
from .cache import patch_cache
from .templates import patch_template
from .middleware import insert_exception_middleware, insert_trace_middleware

from ...internal.logger import get_logger

log = get_logger(__name__)


class TracerConfig(AppConfig):
    name = 'ddtrace.contrib.django'
    label = 'datadog_django'

    def ready(self):
        """
        Ready is called as soon as the registry is fully populated.
        Tracing capabilities must be enabled in this function so that
        all Django internals are properly configured.
        """
        rest_framework_is_installed = apps.is_installed('rest_framework')
        apply_django_patches(patch_rest_framework=rest_framework_is_installed)


def apply_django_patches(patch_rest_framework):
    """
    Ready is called as soon as the registry is fully populated.
    In order for all Django internals are properly configured, this
    must be called after the app is finished starting
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
                # This is the trigger to patch individual connections.
                # By patching these here, all processes including
                # management commands are also traced.
                connections.all()
            except Exception:
                log.exception('error patching Django database connections')

        if settings.INSTRUMENT_CACHE:
            try:
                patch_cache(tracer)
            except Exception:
                log.exception('error patching Django cache')

        # Instrument rest_framework app to trace custom exception handling.
        if patch_rest_framework:
            try:
                from .restframework import patch_restframework
                patch_restframework(tracer)
            except Exception:
                log.exception('error patching rest_framework app')
