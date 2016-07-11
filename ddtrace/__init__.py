"""
"""

from .tracer import Tracer
from .span import Span

__version__ = '0.2.0'

# a global tracer
tracer = Tracer()
