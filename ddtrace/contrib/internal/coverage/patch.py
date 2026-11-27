from ddtrace.testing.internal.coverage_py.patch import patch as _patch
from ddtrace.testing.internal.coverage_py.patch import unpatch as _unpatch


# The coverage.py logic and state belong to Test Optimization: this integration only exposes it to `patch()`.


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"coverage": "*"}


def patch() -> None:
    """Patch Coverage.py to capture coverage percentage when reports are generated."""
    _patch()


def unpatch() -> None:
    """Remove Coverage.py patching."""
    _unpatch()
