import os
from types import ModuleType

from ddtrace.internal.compat import Path
from ddtrace.internal.module import origin


try:
    COLS, _ = os.get_terminal_size()
except Exception:
    COLS = 80
CWD = Path.cwd()


# Taken from Python 3.9. This is not implemented in older versions of Python
def is_relative_to(self, other):
    """Return True if the path is relative to another path or False."""
    try:
        self.relative_to(other)
        return True
    except ValueError:
        return False


def from_editable_install(module: ModuleType, config) -> bool:
    o = origin(module)
    if o is None:
        return False
    return (
        o.is_relative_to(CWD)
        and not any(_.stem.startswith("test") for _ in o.parents)
        and (config.venv is None or not o.is_relative_to(config.venv))
    )


def is_included(module: ModuleType, config) -> bool:
    segments = module.__name__.split(".")
    for i in config.include:
        if i == segments[: len(i)]:
            return True
    return False


def is_ddtrace(module: ModuleType) -> bool:
    name = module.__name__
    return name == "ddtrace" or name.startswith("ddtrace.")
