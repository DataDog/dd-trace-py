"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.settings.env``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. (``import_module``, not ``import x.y as z``: a package attribute can shadow
a submodule, as ``settings.env`` does.) The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

from importlib import import_module
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.settings.env import _ALIAS_TARGETS as _ALIAS_TARGETS  # noqa: F401
    from _ddtrace_internal.settings.env import _CONFIGURATION_ALIASES as _CONFIGURATION_ALIASES  # noqa: F401
    from _ddtrace_internal.settings.env import _DEPRECATED_CONFIGURATIONS as _DEPRECATED_CONFIGURATIONS  # noqa: F401
    from _ddtrace_internal.settings.env import _SUPPORTED_CONFIGURATIONS as _SUPPORTED_CONFIGURATIONS  # noqa: F401
    from _ddtrace_internal.settings.env import EnvConfig as EnvConfig  # noqa: F401
    from _ddtrace_internal.settings.env import _get_env_value as _get_env_value  # noqa: F401
    from _ddtrace_internal.settings.env import _validate_key as _validate_key  # noqa: F401
    from _ddtrace_internal.settings.env import _warned_keys as _warned_keys  # noqa: F401
    from _ddtrace_internal.settings.env import dd_environ as dd_environ  # noqa: F401
    from _ddtrace_internal.settings.env import logger as logger  # noqa: F401
    from _ddtrace_internal.settings.env import (
        register_supported_configurations as register_supported_configurations,  # noqa: F401
    )

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = import_module("_ddtrace_internal.settings.env")
