import logging

# 3rd party
from django.apps import AppConfig

# project
from .db import patch_db
from .conf import settings
from .templates import patch_template

from ...ext import AppTypes


log = logging.getLogger(__name__)


class TracerConfig(AppConfig):
    name = 'ddtrace.contrib.django'

    def ready(self):
        """
        Ready is called as soon as the registry is fully populated.
        Tracing capabilities must be enabled in this function so that
        all Django internals are properly configured.
        """
        if settings.ENABLED:
            tracer = settings.TRACER

            # define the service details
            tracer.set_service_info(
                service=settings.DEFAULT_SERVICE,
                app='django',
                app_type=AppTypes.web,
            )

            # trace Django internals
            try:
                patch_db(tracer)
            except Exception:
                log.exception('error patching Django database connections')

            try:
                patch_template(tracer)
            except Exception:
                log.exception('error patching Django template rendering')
