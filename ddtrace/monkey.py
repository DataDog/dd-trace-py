"""Patch libraries to be automatically instrumented.

It can monkey patch supported standard libraries and third party modules.
A patched module will automatically report spans with its default configuration.

A library instrumentation can be configured (for instance, to report as another service)
using Pin. For that, check its documentation.
"""

from ._monkey import ModuleNotFoundException  # noqa
from ._monkey import PATCH_MODULES  # noqa
from ._monkey import PatchException  # noqa
from ._monkey import get_patched_modules  # noqa
from ._monkey import patch  # noqa
from ._monkey import patch_all  # noqa
from ._monkey import patch_module  # noqa
from .internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.monkey",
    message="Import the patch and patch_all functions directly from the ddtrace module instead",
    version="1.0.0",
)
