"""
"""

from .tracer import Tracer
from .span import Span

__version__ = '0.3.0'

# a global tracer
tracer = Tracer()
