import ctypes
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.appsec._constants import DEFAULT
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


LOGGER = get_logger(__name__)

if asm_config._asm_libddwaf_available:
    try:
        from .ddwaf_types import DDWafRulesType
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
        from .ddwaf_types import py_ddwaf_known_addresses
        from .ddwaf_types import py_ddwaf_update

        _DDWAF_LOADED = True
    except Exception:
        _DDWAF_LOADED = False
        LOGGER.warning("DDWaf features disabled. WARNING: Dynamic Library not loaded", exc_info=True)
else:
    _DDWAF_LOADED = False


class DDWaf_result(object):
    __slots__ = ["data", "actions", "runtime", "total_runtime", "timeout", "truncation", "derivatives"]

    def __init__(
        self,
        data: List[Dict[str, Any]],
        actions: Dict[str, Any],
        runtime: float,
        total_runtime: float,
        timeout: bool,
        truncation: int,
        derivatives: Dict[str, Any],
    ):
        self.data = data
        self.actions = actions
        self.runtime = runtime
        self.total_runtime = total_runtime
        self.timeout = timeout
        self.truncation = truncation
        self.derivatives = derivatives

    def __repr__(self):
        return (
            f"DDWaf_result(data: {self.data}, actions: {self.actions}, runtime: {self.runtime},"
            f" total_runtime: {self.total_runtime}, timeout: {self.timeout},"
            f" truncation: {self.truncation}, derivatives: {self.derivatives})"
        )


class DDWaf_info(object):
    __slots__ = ["loaded", "failed", "errors", "version"]

    def __init__(self, loaded: int, failed: int, errors: Dict[str, Any], version: str):
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
        def __init__(
            self,
            ruleset_map: Dict[str, Any],
            obfuscation_parameter_key_regexp: bytes,
            obfuscation_parameter_value_regexp: bytes,
        ):
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
        def required_data(self) -> List[str]:
            return py_ddwaf_known_addresses(self._handle) if self._handle else []

        def _set_info(self, diagnostics: ddwaf_object) -> None:
            info_struct = diagnostics.struct
            rules = info_struct.get("rules", {}) if info_struct else {}  # type: ignore
            errors_result = rules.get("errors", {})
            version = info_struct.get("ruleset_version", "") if info_struct else ""  # type: ignore
            self._info = DDWaf_info(len(rules.get("loaded", [])), len(rules.get("failed", [])), errors_result, version)
            ddwaf_object_free(diagnostics)

        @property
        def info(self) -> DDWaf_info:
            return self._info

        def update_rules(self, new_rules: Dict[str, DDWafRulesType]) -> bool:
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

        def _at_request_start(self) -> Optional[ddwaf_context_capsule]:
            ctx = None
            if self._handle:
                ctx = py_ddwaf_context_init(self._handle)
            if not ctx:
                LOGGER.debug("DDWaf._at_request_start: failure to create the context.")
            return ctx

        def _at_request_end(self) -> None:
            pass

        def run(
            self,
            ctx: ddwaf_context_capsule,
            data: DDWafRulesType,
            ephemeral_data: DDWafRulesType = None,
            timeout_ms: float = DEFAULT.WAF_TIMEOUT,
        ) -> DDWaf_result:
            start = time.time()
            if not ctx:
                LOGGER.debug("DDWaf.run: dry run. no context created.")
                return DDWaf_result([], {}, 0, (time.time() - start) * 1e6, False, 0, {})

            result = ddwaf_result()
            observator = _observator()
            wrapper = ddwaf_object(data, observator=observator)
            wrapper_ephemeral = ddwaf_object(ephemeral_data, observator=observator) if ephemeral_data else None
            error = ddwaf_run(ctx.ctx, wrapper, wrapper_ephemeral, ctypes.byref(result), int(timeout_ms * 1000))
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

    def version() -> str:
        return ddwaf_get_version().decode("UTF-8")

else:
    # Mockup of the DDWaf class doing nothing
    class DDWaf(object):  # type: ignore
        required_data: List[str] = []
        info: DDWaf_info = DDWaf_info(0, 0, {}, "")

        def __init__(
            self,
            rules: Dict[str, Any],
            obfuscation_parameter_key_regexp: bytes,
            obfuscation_parameter_value_regexp: bytes,
        ):
            self._handle = None

        def run(
            self,
            ctx: Any,
            data: Any,
            ephemeral_data: Any = None,
            timeout_ms: float = DEFAULT.WAF_TIMEOUT,
        ) -> DDWaf_result:
            LOGGER.debug("DDWaf features disabled. dry run")
            return DDWaf_result([], {}, 0.0, 0.0, False, 0, {})

        def update_rules(self, _: Dict[str, Any]) -> bool:
            LOGGER.debug("DDWaf features disabled. dry update")
            return False

        def _at_request_start(self) -> None:
            return None

        def _at_request_end(self) -> None:
            pass

    def version() -> str:
        LOGGER.debug("DDWaf features disabled. null version")
        return "0.0.0"
