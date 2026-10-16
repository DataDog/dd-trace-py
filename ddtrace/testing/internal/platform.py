import os
import platform
import sys
import typing as t


class PlatformTag:
    # Architecture
    OS_ARCHITECTURE = "os.architecture"

    # Platform
    OS_PLATFORM = "os.platform"

    # Version
    OS_VERSION = "os.version"

    # Runtime Name
    RUNTIME_NAME = "runtime.name"

    # Runtime Version
    RUNTIME_VERSION = "runtime.version"

    # Test environment id: a unique identifier for the pytest session / uv environment / Python version
    # combination, used to shard skippable-tests requests so the backend returns only the tests relevant
    # to this specific environment.
    TEST_ENVIRONMENT_ID = "test.environment.id"


def get_platform_tags() -> dict[str, str]:
    """Extract configuration facet tags for OS and Python runtime."""
    return {
        PlatformTag.OS_ARCHITECTURE: platform.machine(),
        PlatformTag.OS_PLATFORM: platform.system(),
        PlatformTag.OS_VERSION: platform.release(),
        PlatformTag.RUNTIME_NAME: platform.python_implementation(),
        PlatformTag.RUNTIME_VERSION: platform.python_version(),
    }


def detect_test_environment_id() -> t.Optional[str]:
    """Best-effort auto-detection of a unique test environment id.

    This is used to shard skippable-tests requests so the backend returns only the tests relevant to
    this specific pytest session / virtual environment / Python version combination.

    Detection strategy (first match wins):
      1. ``_DD_CIVISIBILITY_ITR_TEST_ENVIRONMENT_ID`` env var — explicit override from CI.
      2. ``VIRTUAL_ENV`` env var — when set, its basename is used as the id. Tools like riot, tox, and
         ``python -m venv`` set this to the venv path, and dd-trace-py's riot venvs are named by their
         environment hash (e.g. ``.cache/test-environments/<hash>``).
      3. ``sys.prefix`` — when running inside a venv (``sys.prefix != sys.base_prefix``), the basename
         of ``sys.prefix`` is used. This catches ``uv`` ephemeral environments and pyenv-venv setups.

    Returns ``None`` when no virtual environment can be identified (e.g. running in the system Python).
    """
    # 1. Explicit override from CI
    explicit = os.environ.get("_DD_CIVISIBILITY_ITR_TEST_ENVIRONMENT_ID")
    if explicit:
        return explicit

    # 2. VIRTUAL_ENV env var (set by venv, tox, riot's run-tests, etc.)
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        return os.path.basename(virtual_env)

    # 3. sys.prefix when inside a venv (uv ephemeral, pyenv-venv, etc.)
    if sys.prefix != sys.base_prefix:
        return os.path.basename(sys.prefix)

    return None
