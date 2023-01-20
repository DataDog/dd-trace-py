import ctypes
import logging
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any
    from typing import Union
    from ddtrace.appsec.ddwaf.ddwaf_types import DDWafRulesType

from ddtrace.internal.compat import PY3


LOGGER = logging.getLogger(__name__)  # type: logging.Logger

try:
    from .ddwaf_types import ddwaf_config
    from .ddwaf_types import ddwaf_context_destroy
    from .ddwaf_types import ddwaf_context_init
    from .ddwaf_types import ddwaf_destroy
    from .ddwaf_types import ddwaf_get_version
    from .ddwaf_types import ddwaf_init
    from .ddwaf_types import ddwaf_object
    from .ddwaf_types import ddwaf_result_free
    from .ddwaf_types import ddwaf_ruleset_info
    from .ddwaf_types import ddwaf_toggle_rules
    from .ddwaf_types import ddwaf_update_rule_data
    from .ddwaf_types import py_ddwaf_required_addresses
    from .ddwaf_types import py_ddwaf_run

    _DDWAF_LOADED = True
except OSError:
    _DDWAF_LOADED = False
    LOGGER.warning("DDWaf features disabled. WARNING: Dynamic Library not loaded")

# Python 2/3 unicode str compatibility
if PY3:
    unicode = str

#
# Interface as Cython
#

DEFAULT_DDWAF_TIMEOUT_MS = 2


class DDWaf_result(object):
    __slots__ = ["data", "actions", "runtime", "total_runtime"]

    def __init__(self, data, actions, runtime, total_runtime):
        # type: (DDWaf_result, unicode|None, set[unicode], float, float) -> None
        self.data = data
        self.actions = actions
        self.runtime = runtime
        self.total_runtime = total_runtime


class DDWaf_info(object):
    __slots__ = ["loaded", "failed", "errors", "version"]

    def __init__(self, loaded, failed, errors, version):
        # type: (DDWaf_info, int, int, dict[unicode, Any], unicode) -> None
        self.loaded = loaded
        self.failed = failed
        self.errors = errors
        self.version = version

    def __repr__(self):
        return '{"loaded": %d, "failed": %d, "errors": %s, "version": %s}' % (
            self.loaded,
            self.failed,
            str(self.errors),
            self.version,
        )


if _DDWAF_LOADED:

    class DDWaf(object):
        def __init__(self, rules, obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp):
            # type: (DDWaf, Union[None, int, unicode, list[Any], dict[unicode, Any]], unicode, unicode) -> None
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
            # type: (DDWaf) -> DDWaf_info
            if self._info.loaded > 0:
                errors_result = self._info.errors.struct if self._info.failed > 0 else {}
                version = self._info.version
                version = "" if version is None else version.decode("UTF-8")
                return DDWaf_info(self._info.loaded, self._info.failed, errors_result, version)
            return DDWaf_info(0, 0, {}, "")

        def update_rules(self, new_rules):
            # type: (DDWafRulesType) -> int
            rules = ddwaf_object(new_rules)
            result = ddwaf_update_rule_data(self._handle, rules)
            return result

        def toggle_rules(self, rules_config):
            # type: (dict) -> int
            ddwaf_rules_config = ddwaf_object(rules_config)
            result = ddwaf_toggle_rules(self._handle, ddwaf_rules_config)
            return result

        def run(
            self,  # type: DDWaf
            data,  # type: Union[None, int, unicode, list[Any], dict[unicode, Any]]
            timeout_ms=DEFAULT_DDWAF_TIMEOUT_MS,  # type:int
        ):
            # type: (...) -> DDWaf_result
            start = time.time()

            ctx = ddwaf_context_init(self._handle)
            if ctx == 0:
                raise RuntimeError
            try:
                wrapper = ddwaf_object(data)
                error, result = py_ddwaf_run(ctx, wrapper, timeout_ms * 1000)
                return DDWaf_result(
                    result.data.decode("UTF-8") if result.data else None,
                    {result.actions.array[i].decode("UTF-8") for i in range(result.actions.size)},
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
        return ddwaf_get_version().decode("UTF-8")


else:
    # Mockup of the DDWaf class doing nothing
    class DDWaf(object):  # type: ignore
        required_data = []  # type: list[unicode]
        info = {}  # type: dict[unicode, Any]

        def __init__(self, rules, obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp):
            # type: (DDWaf, Union[None, int, unicode, list[Any], dict[unicode, Any]], unicode, unicode) -> None
            pass

        def run(
            self,  # type: DDWaf
            data,  # type: Union[None, int, unicode, list[Any], dict[unicode, Any]]
            timeout_ms=DEFAULT_DDWAF_TIMEOUT_MS,  # type:int
        ):
            # type: (...) -> tuple[unicode, float, float]
            LOGGER.warning("DDWaf features disabled. dry run")
            return ("", 0.0, 0.0)

    def version():
        # type: () -> unicode
        LOGGER.warning("DDWaf features disabled. null version")
        return "0.0.0"
