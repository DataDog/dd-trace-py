from ..internal.utils.deprecation import deprecation
from ..internal.utils.importlib import func_name  # noqa
from ..internal.utils.importlib import module_name  # noqa
from ..internal.utils.importlib import require_modules  # noqa


deprecation(
    name="ddtrace.utils.importlib",
    message="Use `ddtrace.internal.utils.importlib` module instead",
    version="1.0.0",
)
