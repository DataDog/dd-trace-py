from typing import Dict

from cassandra import __version__

from .session import patch  # noqa: F401
from .session import unpatch  # noqa: F401


def _supported_versions() -> Dict[str, str]:
    return {"cassandra": ">=3.24.0"}


def get_version():
    # type: () -> str
    return __version__
