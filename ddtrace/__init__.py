from ._monkey import patch  # noqa: E402
from ._monkey import patch_all
from .internal.utils.deprecation import deprecated  # noqa: E402
from .pin import Pin  # noqa: E402
from .settings import _config as config  # noqa: E402
from .span import Span  # noqa: E402
from .tracer import Tracer  # noqa: E402
from .version import get_version


__version__ = get_version()

# a global tracer instance with integration settings
tracer = Tracer()

__all__ = [
    "patch",
    "patch_all",
    "Pin",
    "Span",
    "tracer",
    "Tracer",
    "config",
]


@deprecated("This method will be removed altogether", "1.0.0")
def install_excepthook():
    """Install a hook that intercepts unhandled exception and send metrics about them."""


@deprecated("This method will be removed altogether", "1.0.0")
def uninstall_excepthook():
    """Uninstall the global tracer except hook."""
