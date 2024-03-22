"""
CI Visibility Service.
This is normally started automatically by including ``ddtrace=1`` or ``--ddtrace`` in the pytest run command.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal.ci_visibility import CIVisibility
    CIVisibility.enable()
"""
from ddtrace import config as ddconfig

from .constants import DEFAULT_CI_VISIBILITY_SERVICE
from .recorder import CIVisibility


ddconfig._add(
    "ci_visibility",
    {
        "_default_service": DEFAULT_CI_VISIBILITY_SERVICE,
    },
)

__all__ = ["CIVisibility"]
