import ctypes
import time
from typing import TYPE_CHECKING

from six import text_type

from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Any
    from typing import Union

    from ddtrace.appsec.ddwaf.ddwaf_types import DDWafRulesType


LOGGER = get_logger(__name__)

try:
    from .ddwaf_types import ddwaf_config
    from .ddwaf_types import ddwaf_context_destroy
    from .ddwaf_types import ddwaf_context_init
    from .ddwaf_types import ddwaf_destroy
    from .ddwaf_types import ddwaf_get_version
    from .ddwaf_types import ddwaf_init
    from .ddwaf_types import ddwaf_object
    from .ddwaf_types import ddwaf_result
    from .ddwaf_types import ddwaf_result_free
    from .ddwaf_types import ddwaf_ruleset_info
    from .ddwaf_types import ddwaf_run
    from .ddwaf_types import ddwaf_update
    from .ddwaf_types import py_ddwaf_required_addresses

    _DDWAF_LOADED = True
except OSError:
    _DDWAF_LOADED = False
    LOGGER.warning("DDWaf features disabled. WARNING: Dynamic Library not loaded", exc_info=True)

#
# Interface as Cython
#

DEFAULT_DDWAF_TIMEOUT_MS = 2


class DDWaf_result(object):
    __slots__ = ["data", "actions", "runtime", "total_runtime"]

    def __init__(self, data, actions, runtime, total_runtime):
        # type: (DDWaf_result, text_type|None, list[text_type], float, float) -> None
        self.data = data
        self.actions = actions
        self.runtime = runtime
        self.total_runtime = total_runtime


class DDWaf_info(object):
    __slots__ = ["loaded", "failed", "errors", "version"]

    def __init__(self, loaded, failed, errors, version):
        # type: (DDWaf_info, int, int, dict[text_type, Any], text_type) -> None
        self.loaded = loaded
        self.failed = failed
        self.errors = errors
        self.version = version

    def __repr__(self):
        return "{loaded: %d, failed: %d, errors: %s, version: %s}" % (
            self.loaded,
            self.failed,
            str(self.errors),
            self.version,
        )


if _DDWAF_LOADED:

    class DDWaf(object):
        def __init__(self, ruleset_map, obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp):
            # type: (DDWaf, dict[text_type, Any], text_type, text_type) -> None
            config = ddwaf_config(
                key_regex=obfuscation_parameter_key_regexp, value_regex=obfuscation_parameter_value_regexp
            )
            self._info = ddwaf_ruleset_info()
            self._ruleset_map = ddwaf_object.create_without_limits(ruleset_map)
            self._handle = ddwaf_init(self._ruleset_map, ctypes.byref(config), ctypes.byref(self._info))
            self._ctx = 0
            if not self._handle or self._info.failed:
                LOGGER.error(
                    "DDWAF.__init__: invalid rules\n ruleset: %s\nloaded:%s\nerrors:%s\n",
                    self._ruleset_map.struct,
                    self._info.loaded,
                    self.info.errors,
                )

        @property
        def required_data(self):
            # type: (DDWaf) -> list[text_type]
            return py_ddwaf_required_addresses(self._handle) if self._handle else []

        @property
        def info(self):
            # type: (DDWaf) -> DDWaf_info
            errors_result = self._info.errors.struct if self._info.failed > 0 else {}
            version = self._info.version
            version = "" if version is None else version.decode("UTF-8")
            return DDWaf_info(self._info.loaded, self._info.failed, errors_result, version)

        def update_rules(self, new_rules):
            # type: (dict[text_type, DDWafRulesType]) -> bool
            """update the rules of the WAF instance. return True if an error occurs."""
            rules = ddwaf_object.create_without_limits(new_rules)
            result = ddwaf_update(self._handle, rules, ctypes.byref(self._info))
            if result == 0 or result is None:
                LOGGER.error("DDWAF.update_rules: invalid rules")
                return False
            else:
                LOGGER.debug("DDWAF.update_rules success.\ninfo %s", self.info)
                if self._handle:
                    ddwaf_destroy(self._handle)
                if self._ctx:
                    ddwaf_context_destroy(self._ctx)
                self._handle = result
                return True

        def _at_request_start(self):
            if self._ctx:
                ddwaf_context_destroy(self._ctx)
            self._ctx = ddwaf_context_init(self._handle)
            if self._ctx == 0:
                LOGGER.error("DDWaf failure to create the context")

        def _at_request_end(self):
            if self._ctx:
                ddwaf_context_destroy(self._ctx)
                self._ctx = 0

        def run(
            self,  # type: DDWaf
            data,  # type: DDWafRulesType
            timeout_ms=DEFAULT_DDWAF_TIMEOUT_MS,  # type:int
        ):
            # type: (...) -> DDWaf_result
            start = time.time()

            if self._ctx == 0:
                LOGGER.warning("DDWaf failsafe to create the context")
                self._ctx = ddwaf_context_init(self._handle)

            if self._ctx == 0:
                LOGGER.error("DDWaf failure: no context created")
                return DDWaf_result(None, [], 0, (time.time() - start) * 1e6)

            result = ddwaf_result()
            wrapper = ddwaf_object(data)
            error = ddwaf_run(self._ctx, wrapper, ctypes.byref(result), timeout_ms * 1000)
            if error < 0:
                LOGGER.warning("run DDWAF error: %d\ninput %s\nerror %s", error, wrapper.struct, self.info.errors)
            try:
                return DDWaf_result(
                    result.data.decode("UTF-8", errors="ignore") if hasattr(result, "data") and result.data else None,
                    [result.actions.array[i].decode("UTF-8", errors="ignore") for i in range(result.actions.size)],
                    result.total_runtime / 1e3,
                    (time.time() - start) * 1e6,
                )
            finally:
                ddwaf_result_free(ctypes.byref(result))

        def __dealloc__(self):
            if self._ctx:
                ddwaf_context_destroy(self._ctx)
            if self._handle:
                ddwaf_destroy(self._handle)

    def version():
        # type: () -> text_type
        return ddwaf_get_version().decode("UTF-8")


else:
    # Mockup of the DDWaf class doing nothing
    class DDWaf(object):  # type: ignore
        required_data = []  # type: list[text_type]
        info = DDWaf_info(0, 0, {}, "")  # type: DDWaf_info

        def __init__(self, rules, obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp):
            # type: (DDWaf, Union[None, int, text_type, list[Any], dict[text_type, Any]], text_type, text_type) -> None
            pass

        def run(
            self,  # type: DDWaf
            data,  # type: Union[None, int, text_type, list[Any], dict[text_type, Any]]
            timeout_ms=DEFAULT_DDWAF_TIMEOUT_MS,  # type:int
        ):
            # type: (...) -> DDWaf_result
            LOGGER.warning("DDWaf features disabled. dry run")
            return DDWaf_result(None, [], 0.0, 0.0)

        def update_rules(self, _):
            # type: (dict[text_type, DDWafRulesType]) -> bool
            LOGGER.warning("DDWaf features disabled. dry update")
            return True

        def _at_request_start(self):
            pass

        def _at_request_end(self):
            pass

    def version():
        # type: () -> text_type
        LOGGER.warning("DDWaf features disabled. null version")
        return "0.0.0"
