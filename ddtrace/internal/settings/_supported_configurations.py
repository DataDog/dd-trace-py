"""
Legacy import path for ``ddtrace.internal.settings._supported_configurations``, which moved to
``ddtrace_base.settings._supported_configurations``.

This module is a thin re-export shim so external consumers of the historical
``ddtrace.internal.settings._supported_configurations`` import path keep working..
Import from ````ddtrace_base.settings._supported_configurations```` instead.
"""

from ddtrace_base.settings._supported_configurations import CONFIGURATION_ALIASES as CONFIGURATION_ALIASES  # noqa: F401
from ddtrace_base.settings._supported_configurations import (
    DEPRECATED_CONFIGURATIONS as DEPRECATED_CONFIGURATIONS,  # noqa: F401
)
from ddtrace_base.settings._supported_configurations import (
    SENSITIVE_CONFIGURATIONS as SENSITIVE_CONFIGURATIONS,  # noqa: F401
)
from ddtrace_base.settings._supported_configurations import (
    SUPPORTED_CONFIGURATIONS as SUPPORTED_CONFIGURATIONS,  # noqa: F401
)
