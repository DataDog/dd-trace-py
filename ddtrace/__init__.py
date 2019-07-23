import pkg_resources

from .monkey import patch, patch_all
from .pin import Pin
from .span import Span
from .tracer import Tracer
from .settings import config


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
