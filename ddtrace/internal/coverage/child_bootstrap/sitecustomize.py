"""Start-up hook of the Python child processes that a test starts, for native per-test line coverage.

A test process that follows its child processes puts this directory first on PYTHONPATH, so every
Python process it starts imports this file: on CPython 3.12+ it starts recording the child's lines
(see _ddtrace_child_coverage.py). It then imports the sitecustomize it shadows, if there is one.
Children of any Python version read this file, so it keeps to syntax that all of them parse.
"""

import os
import sys


if __name__ == "sitecustomize":
    if sys.version_info >= (3, 12) and os.environ.get("_DD_COVERAGE_CHILD_DIR"):
        try:
            from _ddtrace_child_coverage import start  # type: ignore[import-not-found]

            start()
        except Exception:  # nosec: never break the child
            pass

    # This directory leaves sys.path for good, so the child's program sees the path it would see
    # without it. PYTHONPATH still carries it to the child's own children.
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here in sys.path:
        sys.path.remove(_here)
        _own = sys.modules.pop("sitecustomize", None)
        try:
            import sitecustomize  # noqa: F401
        except ImportError as _error:
            if _own is not None:
                sys.modules["sitecustomize"] = _own
            # As site does: only a missing sitecustomize is silent.
            if getattr(_error, "name", None) != "sitecustomize":
                raise
