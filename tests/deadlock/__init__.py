"""
Deadlock detector for dd-trace-py CI tests.

Wraps the ``_deadlock`` C extension. When the extension has not been built
(e.g. in environments that skip native builds) every public function is a
no-op so the fixture degrades gracefully.

Usage via the pytest fixture (autouse, defined in tests/conftest.py):

    # arm() is called before each test, disarm() after
    # Timeout defaults to 300 s (5 min); set DD_TEST_DEADLOCK_TIMEOUT to override.

Manual use::

    from tests.deadlock import arm, disarm
    arm(timeout=60, test_name="my_test")
    try:
        ...
    finally:
        disarm()
"""

from typing import Optional


try:
    from ._deadlock import arm  # noqa: F401
    from ._deadlock import disarm  # noqa: F401

    DEADLOCK_AVAILABLE = True
except ImportError:
    DEADLOCK_AVAILABLE = False

    def arm(timeout: int = 300, enable_gdb: bool = False, test_name: Optional[str] = None) -> None:
        """No-op: _deadlock extension not built."""

    def disarm() -> None:
        """No-op: _deadlock extension not built."""
