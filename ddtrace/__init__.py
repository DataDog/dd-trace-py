import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict

LOADED_MODULES = frozenset(sys.modules.keys())

# Ensure we capture references to unpatched modules as early as possible
import ddtrace.internal._unpatched  # noqa
from ._logger import configure_ddtrace_logger

# configure ddtrace logger before other modules log
configure_ddtrace_logger()  # noqa: E402

from .internal._instrumentation_enabled import _INSTRUMENTATION_ENABLED

from .internal._stubs_core import get_config

config = get_config()

if _INSTRUMENTATION_ENABLED:
    # Enable telemetry writer and excepthook as early as possible to ensure we capture any exceptions from initialization
    import ddtrace.internal.telemetry  # noqa: E402

from ._monkey import patch  # noqa: E402
from ._monkey import patch_all  # noqa: E402

from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402

from .version import get_version  # noqa: E402

__version__ = get_version()

import ddtrace.internal.initialization  # noqa: E402

if _INSTRUMENTATION_ENABLED:
    # Initialize additional components

    import os

    # TODO: Deprecate accessing tracer from ddtrace.__init__ module in v4.0
    if os.environ.get("_DD_GLOBAL_TRACER_INIT", "true").lower() in ("1", "true"):
        from ddtrace.trace import tracer  # noqa: F401

__all__ = ["patch", "patch_all", "config", "DDTraceDeprecationWarning"]
