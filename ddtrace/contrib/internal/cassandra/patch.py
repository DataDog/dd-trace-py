from typing import Dict

from .session import get_version  # noqa: F401
from .session import patch  # noqa: F401
from .session import unpatch  # noqa: F401


def _supported_versions() -> Dict[str, str]:
    return {"cassandra": ">=3.24.0"}
