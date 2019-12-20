from django.apps import AppConfig

from ddtrace.vendor.debtcollector import removals
from ...internal.logger import get_logger

log = get_logger(__name__)


@removals.removed_class("TracerConfig")
class TracerConfig(AppConfig):
    name = "ddtrace.contrib.django"
    label = "datadog_django"

    def ready(self):
        """
        Ready formerly was used to initiate patching of Django and DRF.
        """
        pass
