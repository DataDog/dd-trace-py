
from .tracer import Tracer
from .span import Span # noqa

__version__ = '0.3.7'

# a global tracer
tracer = Tracer()
