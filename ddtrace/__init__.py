from .monkey import patch, patch_all
from .pin import Pin
from .span import Span
from .tracer import Tracer
from .settings import Config

__version__ = '0.14.1'

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
