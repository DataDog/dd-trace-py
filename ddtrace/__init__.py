from .install import patch, patch_all
from .pin import Pin
from .settings import Config
from .span import Span
from .tracer import Tracer

__version__ = '0.15.0'

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
