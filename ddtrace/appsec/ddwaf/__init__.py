import ctypes
import time
from typing import TYPE_CHECKING

from six import text_type

from ddtrace.appsec._constants import DEFAULT
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Any
    from typing import Union

    from ddtrace.appsec.ddwaf.ddwaf_types import DDWafRulesType


LOGGER = get_logger(__name__)

try:
    from .ddwaf_types import ddwaf_config
    from .ddwaf_types import ddwaf_context_capsule
    from .ddwaf_types import ddwaf_get_version
    from .ddwaf_types import ddwaf_object
    from .ddwaf_types import ddwaf_object_free
    from .ddwaf_types import ddwaf_result
    from .ddwaf_types import ddwaf_ruleset_info
    from .ddwaf_types import ddwaf_run
    from .ddwaf_types import py_ddwaf_context_init
    from .ddwaf_types import py_ddwaf_init
    from .ddwaf_types import py_ddwaf_required_addresses
    from .ddwaf_types import py_ddwaf_update

    _DDWAF_LOADED = True
except OSError:
    _DDWAF_LOADED = False
    LOGGER.warning("DDWaf features disabled. WARNING: Dynamic Library not loaded", exc_info=True)

#
# Interface as Cython
#


class DDWaf_result(object):
    __slots__ = ["data", "actions", "runtime", "total_runtime", "timeout"]

    def __init__(self, data, actions, runtime, total_runtime, timeout):
        # type: (DDWaf_result, text_type|None, list[text_type], float, float, bool) -> None
        self.data = data
        self.actions = actions
        self.runtime = runtime
        self.total_runtime = total_runtime
        self.timeout = timeout


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
            ruleset_map_object = ddwaf_object.create_without_limits(ruleset_map)
            self._handle = py_ddwaf_init(ruleset_map_object, ctypes.byref(config), ctypes.byref(self._info))
            if not self._handle or self._info.failed:
                # We keep the handle alive in case of errors, as some valid rules can be loaded
                # at the same time some invalid ones are rejected
                LOGGER.debug(
                    "DDWAF.__init__: invalid rules\n ruleset: %s\nloaded:%s\nerrors:%s\n",
                    ruleset_map_object.struct,
                    self._info.loaded,
                    self.info.errors,
                )
            ddwaf_object_free(ctypes.byref(ruleset_map_object))

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
            result = py_ddwaf_update(self._handle, rules, self._info)
            ddwaf_object_free(rules)
            if result:
                LOGGER.debug("DDWAF.update_rules success.\ninfo %s", self.info)
                self._handle = result
                return True
            else:
                LOGGER.debug("DDWAF.update_rules: keeping the previous handle.")
                return False

        def _at_request_start(self):
            # type: () -> ddwaf_context_capsule
            if self._handle:
                ctx = py_ddwaf_context_init(self._handle)
            if not ctx:
                LOGGER.debug("DDWaf._at_request_start: failure to create the context.")
            return ctx

        def _at_request_end(self):
            # () -> None
            pass

        def run(
            self,  # type: DDWaf
            ctx,  # type: ddwaf_context_capsule
            data,  # type: DDWafRulesType
            timeout_ms=DEFAULT.WAF_TIMEOUT,  # type:float
        ):
            # type: (...) -> DDWaf_result
            start = time.time()

            if not ctx:
                LOGGER.debug("DDWaf.run: dry run. no context created.")
                return DDWaf_result(None, [], 0, (time.time() - start) * 1e6, False)

            result = ddwaf_result()
            wrapper = ddwaf_object(data)
            error = ddwaf_run(ctx.ctx, wrapper, ctypes.byref(result), int(timeout_ms * 1000))
            if error < 0:
                LOGGER.debug("run DDWAF error: %d\ninput %s\nerror %s", error, wrapper.struct, self.info.errors)
            return DDWaf_result(
                result.data.decode("UTF-8", errors="ignore") if hasattr(result, "data") and result.data else None,
                [result.actions.array[i].decode("UTF-8", errors="ignore") for i in range(result.actions.size)],
                result.total_runtime / 1e3,
                (time.time() - start) * 1e6,
                result.timeout,
            )

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
            ctx,  # type: ddwaf_context_capsule
            data,  # type: Union[None, int, text_type, list[Any], dict[text_type, Any]]
            timeout_ms=DEFAULT.WAF_TIMEOUT,  # type:float
        ):
            # type: (...) -> DDWaf_result
            LOGGER.debug("DDWaf features disabled. dry run")
            return DDWaf_result(None, [], 0.0, 0.0, False)

        def update_rules(self, _):
            # type: (dict[text_type, DDWafRulesType]) -> bool
            LOGGER.debug("DDWaf features disabled. dry update")
            return False

        def _at_request_start(self):
            pass

        def _at_request_end(self):
            pass

    def version():
        # type: () -> text_type
        LOGGER.debug("DDWaf features disabled. null version")
        return "0.0.0"
