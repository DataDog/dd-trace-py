from ddtrace import config
from .patch import patch, unpatch

__all__ = ["patch", "unpatch"]

if config.get_from("_ci_visibility_unittest_enabled"):
    patch()
