from typing import Dict

import pymongo

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.vendor.sqlcommenter import _generate_comment_from_metadata as _generate_comment_from_metadata

from ....internal.schema import schematize_service_name
from .client import patch_pymongo_sync_modules
from .client import unpatch_pymongo_sync_modules
from .utils import dbm_comment_injector


log = get_logger(__name__)


config._add(
    "pymongo",
    dict(
        _default_service=schematize_service_name("pymongo"),
        _dbm_propagator=_DBM_Propagator(2, "spec", dbm_comment_injector, _generate_comment_from_metadata),
    ),
)


def get_version():
    # type: () -> str
    return getattr(pymongo, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"pymongo": ">=3.8.0"}


def patch():
    """Patch pymongo synchronous and asynchronous modules."""
    if getattr(pymongo, "_datadog_patch", False):
        return
    patch_pymongo_sync_modules()
    if pymongo.version_tuple >= (4, 12):
        from .async_client import patch_pymongo_async_modules

        patch_pymongo_async_modules()
    pymongo._datadog_patch = True


def unpatch():
    """Unpatch pymongo synchronous and asynchronous modules."""
    if not getattr(pymongo, "_datadog_patch", False):
        return
    unpatch_pymongo_sync_modules()
    if pymongo.version_tuple >= (4, 12):
        from .async_client import unpatch_pymongo_async_modules

        unpatch_pymongo_async_modules()
    pymongo._datadog_patch = False
