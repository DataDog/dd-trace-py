from ..internal.utils.importlib import func_name  # noqa
from ..internal.utils.importlib import module_name  # noqa
from ..internal.utils.importlib import require_modules  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.importlib",
    removal_version="1.0.0",
)
