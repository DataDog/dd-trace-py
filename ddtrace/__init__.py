import pkg_resources

# Always import and patch import hooks before loading anything else
from .internal.import_hooks import patch as patch_import_hooks
patch_import_hooks()  # noqa: E402

from .monkey import patch, patch_all
from .pin import Pin
from .span import Span
from .tracer import Tracer
from .settings import config
from .utils.deprecation import deprecated

try:
    __version__ = pkg_resources.get_distribution(__name__).version
except pkg_resources.DistributionNotFound:
    # package is not installed
    __version__ = None


# a global tracer instance with integration settings
tracer = Tracer()

__all__ = [
    'patch',
    'patch_all',
    'Pin',
    'Span',
    'tracer',
    'Tracer',
    'config',
]


@deprecated('This method will be removed altogether', '1.0.0')
def install_excepthook():
    """Install a hook that intercepts unhandled exception and send metrics about them."""


@deprecated('This method will be removed altogether', '1.0.0')
def uninstall_excepthook():
    """Uninstall the global tracer except hook."""
