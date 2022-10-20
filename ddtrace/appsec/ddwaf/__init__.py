import ctypes
import time
from typing import Any

from .ddwaf_types import ddwaf_config
from .ddwaf_types import ddwaf_context_destroy
from .ddwaf_types import ddwaf_context_init
from .ddwaf_types import ddwaf_destroy
from .ddwaf_types import ddwaf_get_version
from .ddwaf_types import ddwaf_init
from .ddwaf_types import ddwaf_object
from .ddwaf_types import ddwaf_result_free
from .ddwaf_types import ddwaf_ruleset_info
from .ddwaf_types import obj_struct
from .ddwaf_types import py_ddwaf_required_addresses
from .ddwaf_types import py_ddwaf_run
from .ddwaf_types import unicode


#
# Interface as Cython
#

DEFAULT_DDWAF_TIMEOUT_MS = 20


class DDWaf(object):
    def __init__(self, rules, obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp):
        # type: (DDWaf, obj_struct, unicode, unicode) -> None
        config = ddwaf_config(
            key_regex=obfuscation_parameter_key_regexp, value_regex=obfuscation_parameter_value_regexp
        )
        self._info = ddwaf_ruleset_info()
        self._rules = ddwaf_object(rules)
        self._handle = ddwaf_init(self._rules, ctypes.byref(config), ctypes.byref(self._info))
        if not self._handle:
            raise ValueError("DDWAF.__init__: invalid rules")

    @property
    def required_data(self):
        # type: (DDWaf) -> list[unicode]
        return py_ddwaf_required_addresses(self._handle)

    @property
    def info(self):
        # type: (DDWaf) -> dict[unicode, Any]
        if self._info.loaded > 0:
            errors_result = self._info.errors.struct if self._info.failed > 0 else {}
            version = self._info.version
            version = "" if version is None else version.decode("UTF-8")
            return {
                "loaded": self._info.loaded,
                "failed": self._info.failed,
                "errors": errors_result,
                "version": version,
            }
        return {
            "loaded": 0,
            "failed": 0,
            "errors": {},
            "version": "",
        }

    def run(self, data, timeout_ms=DEFAULT_DDWAF_TIMEOUT_MS):
        # type: (DDWaf, obj_struct, int) -> tuple[unicode, float, float]
        start = time.time()

        ctx = ddwaf_context_init(self._handle)
        if ctx == 0:
            raise RuntimeError
        try:
            wrapper = ddwaf_object(data)
            error, result = py_ddwaf_run(ctx, wrapper, timeout_ms * 1000)
            return (
                result.data.decode("UTF-8") if result.data else None,
                result.total_runtime / 1e3,
                (time.time() - start) * 1e6,
            )
        finally:
            ddwaf_result_free(ctypes.byref(result))
            ddwaf_context_destroy(ctx)

    def __dealloc__(self):
        ddwaf_destroy(self._handle)


def version():
    # type: () -> unicode
    return ddwaf_get_version().decode('UTF-8')