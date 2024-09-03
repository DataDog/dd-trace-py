"""
NOTE: BETA - this API is currently in development and is subject to change.
"""

import os

from ddtrace import config
from ddtrace.internal.utils.formats import asbool


# Default test visibility settings
config._add(
    "test_visibility",
    dict(
        _default_service="default_test_visibility_service",
        itr_skipping_level="suite" if asbool(os.getenv("_DD_CIVISIBILITY_ITR_SUITE_MODE")) else "test",
    ),
)


def get_version() -> str:
    return "0.0.1"


__all__ = ["get_version"]
