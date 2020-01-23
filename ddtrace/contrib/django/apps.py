from django.apps import AppConfig

from ddtrace.vendor.debtcollector import removals
from ...internal.logger import get_logger
from .patch import patch

log = get_logger(__name__)


@removals.removed_class(
    "TracerConfig",
    message="""Adding ddtrace.contrib.django to settings.py is no longer required, instead do:
    import ddtrace
    ddtrace.patch_all()
    """,
)
class TracerConfig(AppConfig):
    name = "ddtrace.contrib.django"
    label = "datadog_django"

    def ready(self):
        """
        Ready formerly was used to initiate patching of Django and DRF.
        """
        # Make sure our app is configured
        patch()
