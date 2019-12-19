from django.apps import AppConfig

from ...internal.logger import get_logger
from ...utils.deprecation import deprecated

log = get_logger(__name__)


class TracerConfig(AppConfig):
    name = 'ddtrace.contrib.django'
    label = 'datadog_django'

    @deprecated((
        'The ddtrace.contrib.django app has been deprecated in favour of more precise instrumentation. '
        'Remove ddtrace.contrib.django from INSTALLED_APPS to remove this warning.'
    ))
    def ready(self):
        """
        Ready formerly was used to initiate patching of Django and DRF.

        To maintain backwards compatibility it now serves to pull any Django
        configuration from settings.py
        """
        pass
