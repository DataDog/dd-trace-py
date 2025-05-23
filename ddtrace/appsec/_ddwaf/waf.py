import ctypes
import json
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_config
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_get_version
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object_free
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_run
from ddtrace.appsec._ddwaf.ddwaf_types import py_add_or_update_config
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_builder_build_instance
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_builder_init
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_context_init
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_known_addresses
from ddtrace.appsec._ddwaf.ddwaf_types import py_remove_config
from ddtrace.appsec._ddwaf.waf_stubs import WAF
from ddtrace.appsec._ddwaf.waf_stubs import DDWaf_info
from ddtrace.appsec._ddwaf.waf_stubs import DDWaf_result
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule
from ddtrace.appsec._utils import _observator
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import PayloadType


LOGGER = get_logger(__name__)


DDWAF_ERR_INTERNAL = -3
DDWAF_ERR_INVALID_OBJECT = -2
DDWAF_ERR_INVALID_ARGUMENT = -1
DDWAF_OK = 0
DDWAF_MATCH = 1

ASM_DD_DEFAULT = "ASM_DD/default"


class DDWaf(WAF):
    empty_observator = _observator()

    def __init__(
        self,
        ruleset_map: Dict[str, Any],
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
        metrics,
    ):
        # avoid circular import

        self.report_error = metrics._set_waf_error_log
        config = ddwaf_config(
            key_regex=obfuscation_parameter_key_regexp, value_regex=obfuscation_parameter_value_regexp
        )
        diagnostics = ddwaf_object()
        ruleset_map_object = ddwaf_object.create_without_limits(ruleset_map)
        self._builder = py_ddwaf_builder_init(config)
        py_add_or_update_config(self._builder, ASM_DD_DEFAULT, ruleset_map_object, diagnostics)
        self._handle = py_ddwaf_builder_build_instance(self._builder)
        self._cached_version = ""
        self._asm_dd_cache = {ASM_DD_DEFAULT}
        self._set_info(diagnostics, "init")
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
        self._default_ruleset = ruleset_map_object
        metrics.ddwaf_version = version()

    @property
    def required_data(self) -> List[str]:
        return py_ddwaf_known_addresses(self._handle) if self._handle else []

    def _set_info(self, diagnostics: ddwaf_object, action: str) -> None:
        info_struct: dict[str, Any] = diagnostics.struct or {}  # type: ignore
        rules = info_struct.get("rules", {})
        errors_result = rules.get("errors", {})
        version = info_struct.get("ruleset_version", self._cached_version)
        self._cached_version = version
        for key, value in info_struct.items():
            if isinstance(value, dict):
                if value.get("error", False):
                    self.report_error(f"appsec.waf.error::{action}::{key}::{value['error']}", self._cached_version)
                elif value.get("errors", False):
                    self.report_error(
                        f"appsec.waf.error::{action}::{key}::{str(value['errors'])}", self._cached_version, False
                    )
        self._info = DDWaf_info(
            len(rules.get("loaded", [])),
            len(rules.get("failed", [])),
            json.dumps(errors_result, separators=(",", ":")) if errors_result else "",
            version,
        )
        ddwaf_object_free(diagnostics)

    @property
    def info(self) -> DDWaf_info:
        return self._info

    def update_rules(
        self, removals: Sequence[Tuple[str, str]], updates: Sequence[Tuple[str, str, PayloadType]]
    ) -> bool:
        """update the rules of the WAF instance. return False if an error occurs."""
        ok = True
        for product, path in removals:
            ok &= py_remove_config(self._builder, path)
            if product == "ASM_DD":
                self._asm_dd_cache.discard(path)
        for product, path, rules in updates:
            if product == "ASM_DD":
                if ASM_DD_DEFAULT in self._asm_dd_cache:
                    # we need to remove the default ruleset before adding the new one
                    ok &= py_remove_config(self._builder, ASM_DD_DEFAULT)
                    self._asm_dd_cache.discard(ASM_DD_DEFAULT)
                self._asm_dd_cache.add(path)
            diagnostics = ddwaf_object()
            ruleset_object = ddwaf_object.create_without_limits(rules)
            ok &= py_add_or_update_config(self._builder, path, ruleset_object, diagnostics)
            self._set_info(diagnostics, "update")
            ddwaf_object_free(ruleset_object)
            ddwaf_object_free(diagnostics)
        if not self._asm_dd_cache:
            # we need to add the default ruleset back
            diagnostics = ddwaf_object()
            ok &= py_add_or_update_config(self._builder, ASM_DD_DEFAULT, self._default_ruleset, diagnostics)
            self._set_info(diagnostics, "update")
            ddwaf_object_free(diagnostics)
            self._asm_dd_cache.add(ASM_DD_DEFAULT)
        new_handle = py_ddwaf_builder_build_instance(self._builder)
        if new_handle:
            self._handle = new_handle
        return ok

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
        start = time.monotonic()
        if not ctx:
            LOGGER.debug("DDWaf.run: dry run. no context created.")
            return DDWaf_result(0, [], {}, 0, (time.time() - start) * 1e6, False, self.empty_observator, {})

        result_obj = ddwaf_object()
        observator = _observator()
        wrapper = ddwaf_object(data, observator=observator)
        wrapper_ephemeral = ddwaf_object(ephemeral_data, observator=observator) if ephemeral_data else None
        error = ddwaf_run(ctx.ctx, wrapper, wrapper_ephemeral, ctypes.byref(result_obj), int(timeout_ms * 1000))
        if error < 0:
            LOGGER.debug("run DDWAF error: %d\ninput %s\nerror %s", error, wrapper.struct, self.info.errors)
        result = result_obj.struct
        if error == DDWAF_ERR_INTERNAL or not isinstance(result, dict):
            # result is not valid
            ddwaf_object_free(result_obj)
            return DDWaf_result(error, [], {}, 0, 0, False, self.empty_observator, {})
        main_res = DDWaf_result(
            error,
            result["events"],
            result["actions"],
            result["duration"] / 1e3,
            (time.monotonic() - start) * 1e6,
            result["timeout"],
            observator,
            result["attributes"],
            result["keep"],
        )
        ddwaf_object_free(result_obj)
        return main_res


    @property
    def initialized(self) -> bool:
        return bool(self._handle)

    def __del__(self):
        if hasattr(self, "_default_ruleset"):
            ddwaf_object_free(ctypes.byref(self._default_ruleset))


def version() -> str:
    return ddwaf_get_version().decode("UTF-8")
