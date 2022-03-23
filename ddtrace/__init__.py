import os

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all
from .internal.utils.formats import asbool  # noqa
from .internal.telemetry import telemetry_writer
from .pin import Pin  # noqa: E402
from .settings import _config as config  # noqa: E402
from .span import Span  # noqa: E402
from .tracer import Tracer  # noqa: E402
from .version import get_version


__version__ = get_version()

# a global tracer instance with integration settings
tracer = Tracer()

# instrumentation telemetry writer should be enabled/started after the global tracer and configs
# are initialized
if asbool(os.getenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED")):
    telemetry_writer.enable()

__all__ = [
    "patch",
    "patch_all",
    "Pin",
    "Span",
    "tracer",
    "Tracer",
    "config",
]
