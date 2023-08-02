import os

from ddtrace import config
from .patch import patch

__all__ = ["patch"]

if config._ci_visibility_unittest_enabled:
    # unittest default settings
    config._add(
        "unittest",
        dict(
            _default_service="unittest",
            operation_name=os.getenv("DD_UNITTEST_OPERATION_NAME", default="unittest.test"),
        ),
    )

    patch()
