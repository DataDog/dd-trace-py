from .patch import unittest_add_option_init, patch



import os

from ddtrace import config

__all__ = ["patch"]

# unittest default settings
config._add(
    "unittest",
    dict(
        _default_service="unittest",
        operation_name=os.getenv("DD_UNITTEST_OPERATION_NAME", default="unittest.test"),
    ),
)

patch()
