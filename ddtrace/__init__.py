import importlib
import sys
import typing


if typing.TYPE_CHECKING:
    from typing import Any
    from typing import List
    from typing import Optional

from ddtrace.internal.module import ModuleWatchdog


ModuleWatchdog.install()

from ._logger import configure_ddtrace_logger  # noqa: E402


# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402
from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402
from .pin import Pin  # noqa: E402
from .settings import _config as config  # noqa: E402
from .span import Span  # noqa: E402
from .version import get_version  # noqa: E402


__version__ = get_version()
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

if sys.version_info >= (3, 7):
    _tracer = None  # type: Optional[Tracer]

    def __getattr__(name):
        # type: (str) -> Any
        if name == "tracer":
            global _tracer
            if _tracer is None:
                from .tracer import Tracer

                _tracer = Tracer()
            return _tracer
        if name == "Tracer":
            from .tracer import Tracer

            return Tracer
        if name in __all__:
            return importlib.import_module("." + name, __name__)

        raise AttributeError("module %r has no attribute %r" % (__name__, name))


else:
    from .tracer import Tracer

    tracer = Tracer()


def __dir__():
    # type: () -> List[str]
    return __all__ + [
        "tracer",
    ]
