import ctypes
import time
from typing import TYPE_CHECKING

from six import text_type

from ddtrace.appsec._constants import DEFAULT
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Any
    from typing import Union

    from .ddwaf_types import DDWafRulesType


LOGGER = get_logger(__name__)

try:
    from .ddwaf_types import _observator
    from .ddwaf_types import ddwaf_config
    from .ddwaf_types import ddwaf_context_capsule
    from .ddwaf_types import ddwaf_get_version
    from .ddwaf_types import ddwaf_object
    from .ddwaf_types import ddwaf_object_free
    from .ddwaf_types import ddwaf_result
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
    __slots__ = ["data", "actions", "runtime", "total_runtime", "timeout", "truncation", "derivatives"]

    def __init__(self, data, actions, runtime, total_runtime, timeout, truncation, derivatives):
        # type: (DDWaf_result, text_type|None, list[text_type], float, float, bool, int, dict[str, Any]) -> None
        self.data = data
        self.actions = actions
        self.runtime = runtime
        self.total_runtime = total_runtime
        self.timeout = timeout
        self.truncation = truncation
        self.derivatives = derivatives


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
            diagnostics = ddwaf_object()
            ruleset_map_object = ddwaf_object.create_without_limits(ruleset_map)
            self._handle = py_ddwaf_init(ruleset_map_object, ctypes.byref(config), ctypes.byref(diagnostics))
            self._set_info(diagnostics)
            info = self.info
            if not self._handle or info.failed:
                # We keep the handle alive in case of errors, as some valid rules can be loaded
                # at the same time some invalid ones are rejected
                LOGGER.debug(
                    "DDWAF.__init__: invalid rules\n ruleset: %s\nloaded:%s\nerrors:%s\n",
                    ruleset_map_object.struct,
                    info.failed,
                    info.errors,
                )
            ddwaf_object_free(ctypes.byref(ruleset_map_object))

        @property
        def required_data(self):
            # type: (DDWaf) -> list[text_type]
            return py_ddwaf_required_addresses(self._handle) if self._handle else []

        def _set_info(self, diagnostics):
            # type: (DDWaf, ddwaf_object) -> None
            info_struct = diagnostics.struct
            rules = info_struct.get("rules", {}) if info_struct else {}  # type: ignore
            errors_result = rules.get("errors", {})
            version = info_struct.get("ruleset_version", "") if info_struct else ""  # type: ignore
            self._info = DDWaf_info(len(rules.get("loaded", [])), len(rules.get("failed", [])), errors_result, version)
            ddwaf_object_free(diagnostics)

        @property
        def info(self):
            # type: (DDWaf) -> DDWaf_info
            return self._info

        def update_rules(self, new_rules):
            # type: (dict[text_type, DDWafRulesType]) -> bool
            """update the rules of the WAF instance. return True if an error occurs."""
            rules = ddwaf_object.create_without_limits(new_rules)
            diagnostics = ddwaf_object()
            result = py_ddwaf_update(self._handle, rules, diagnostics)
            self._set_info(diagnostics)
            ddwaf_object_free(rules)
            if result:
                LOGGER.debug("DDWAF.update_rules success.\ninfo %s", self.info)
                self._handle = result
                return True
            else:
                LOGGER.debug("DDWAF.update_rules: keeping the previous handle.")
                return False

        def _at_request_start(self):
            # type: () -> ddwaf_context_capsule | None
            ctx = None
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
                return DDWaf_result(None, [], 0, (time.time() - start) * 1e6, False, 0, {})

            result = ddwaf_result()
            observator = _observator()
            wrapper = ddwaf_object(data, observator=observator)
            error = ddwaf_run(ctx.ctx, wrapper, ctypes.byref(result), int(timeout_ms * 1000))
            if error < 0:
                LOGGER.debug("run DDWAF error: %d\ninput %s\nerror %s", error, wrapper.struct, self.info.errors)
            return DDWaf_result(
                result.events.struct,
                result.actions.struct,
                result.total_runtime / 1e3,
                (time.time() - start) * 1e6,
                result.timeout,
                observator.truncation,
                result.derivatives.struct,
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
            # type: (DDWaf, dict[text_type, Any], text_type, text_type) -> None
            self._handle = None

        def run(
            self,  # type: DDWaf
            ctx,  # type: ddwaf_context_capsule
            data,  # type: Union[None, int, text_type, list[Any], dict[text_type, Any]]
            timeout_ms=DEFAULT.WAF_TIMEOUT,  # type:float
        ):
            # type: (...) -> DDWaf_result
            LOGGER.debug("DDWaf features disabled. dry run")
            return DDWaf_result(None, [], 0.0, 0.0, False, 0, {})

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
