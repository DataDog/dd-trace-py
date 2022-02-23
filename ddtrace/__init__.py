from ._monkey import patch  # noqa: E402
from ._monkey import patch_all
from .internal.writer import NoopTraceWriter  # noqa: E402
from .pin import Pin  # noqa: E402
from .settings import _config as config  # noqa: E402
from .span import Span  # noqa: E402
from .tracer import Tracer  # noqa: E402
from .version import get_version


__version__ = get_version()

# a global tracer instance with integration settings
tracer = Tracer()

__all__ = [
    "NoopTraceWriter",
    "patch",
    "patch_all",
    "Pin",
    "Span",
    "tracer",
    "Tracer",
    "config",
]
