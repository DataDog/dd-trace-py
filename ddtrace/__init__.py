from ._monkey import patch  # noqa: E402
<<<<<<< HEAD
from ._monkey import patch_all
=======
from ._monkey import patch_all  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
>>>>>>> cf87cf91 (fix(starlette/fastapi): add back removed public functions (#3850))
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
    "DDTraceDeprecationWarning",
]
