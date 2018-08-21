import logging
from .monkey import patch, patch_all
from .pin import Pin
from .span import Span
from .tracer import Tracer
from .settings import Config

__version__ = '0.13.0'

# configure the root logger
logging.basicConfig()
log = logging.getLogger(__name__)

# a global tracer instance with integration settings
tracer = Tracer()
config = Config()

__all__ = [
    'patch',
    'patch_all',
    'Pin',
    'Span',
    'tracer',
    'Tracer',
    'config',
]
