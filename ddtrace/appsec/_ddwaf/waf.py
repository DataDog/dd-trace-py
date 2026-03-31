import json
import time
from typing import Any
from typing import Optional
from typing import Sequence

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf.ddwaf_types import DEFAULT_ALLOCATOR
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_context_eval
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object_destroy
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_subcontext_eval
from ddtrace.appsec._ddwaf.ddwaf_types import py_add_or_update_config
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_builder_build_instance
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_builder_init
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_context_init
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_known_addresses
from ddtrace.appsec._ddwaf.ddwaf_types import py_ddwaf_subcontext_init
from ddtrace.appsec._ddwaf.ddwaf_types import py_remove_config
from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule
from ddtrace.appsec._metrics import report_error
from ddtrace.appsec._utils import DDWaf_info
from ddtrace.appsec._utils import DDWaf_result
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
OBFUSCATOR_CONFIG_PATH = "obfuscator/config"


class DDWaf:
    empty_observator = _observator()

    def __init__(
        self,
        ruleset_json_str: bytes,
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ) -> None:
        # v2: no ddwaf_config — builder takes no arguments
        self._builder = py_ddwaf_builder_init()

        # v2: obfuscator configured via builder config path
        obfuscator_config: dict[str, Any] = {"obfuscator": {}}
        if obfuscation_parameter_key_regexp:
            obfuscator_config["obfuscator"]["key_regex"] = obfuscation_parameter_key_regexp.decode(
                "UTF-8", errors="ignore"
            )
        if obfuscation_parameter_value_regexp:
            obfuscator_config["obfuscator"]["value_regex"] = obfuscation_parameter_value_regexp.decode(
                "UTF-8", errors="ignore"
            )
        obfuscator_obj = ddwaf_object(obfuscator_config)
        obfuscator_diagnostics = ddwaf_object()
        py_add_or_update_config(self._builder, OBFUSCATOR_CONFIG_PATH, obfuscator_obj, obfuscator_diagnostics)
        ddwaf_object_destroy(obfuscator_obj, DEFAULT_ALLOCATOR)
        ddwaf_object_destroy(obfuscator_diagnostics, DEFAULT_ALLOCATOR)

        diagnostics = ddwaf_object()
        ruleset_map_object = ddwaf_object.from_json_bytes(ruleset_json_str)
        if not ruleset_map_object:
            raise ValueError("Invalid ruleset provided to DDWaf constructor")
        py_add_or_update_config(self._builder, ASM_DD_DEFAULT, ruleset_map_object, diagnostics)
        self._handle = py_ddwaf_builder_build_instance(self._builder)
        self._cached_version = ""
        self._asm_dd_cache = {ASM_DD_DEFAULT}
        self._set_info(diagnostics, "init")
        info = self.info
        if not self._handle or info.failed:
            LOGGER.debug(
                "DDWAF.__init__: invalid rules\n ruleset: %s\nloaded:%s\nerrors:%s\n",
                ruleset_map_object.struct,
                info.failed,
                info.errors,
            )
        self._default_ruleset = ruleset_map_object
        self._rc_products: dict[str, set[str]] = {}
        self._rc_products_str: str = ""
        self._rc_updates: int = 0
        self._lifespan: int = 0

    @property
    def required_data(self) -> list[str]:
        return py_ddwaf_known_addresses(self._handle) if self._handle else []

    def _set_info(self, diagnostics: ddwaf_object, action: str) -> None:
        info_struct: dict[str, Any] = diagnostics.struct or {}  # type: ignore
        rules = info_struct.get("rules", {})
        errors_result = rules.get("errors", {})
        version = info_struct.get("ruleset_version", self._cached_version)
        self._cached_version = version
        for key, value in info_struct.items():
            if isinstance(value, dict):
                if error := value.get("error", False):
                    report_error(f"appsec.waf.error::{action}::{key}::{error}", self._cached_version, action)
                elif errors := value.get("errors", False):
                    report_error(
                        f"appsec.waf.error::{action}::{key}::{str(errors)}",
                        self._cached_version,
                        action,
                        False,
                    )
        self._info = DDWaf_info(
            len(rules.get("loaded", [])),
            len(rules.get("failed", [])),
            json.dumps(errors_result, separators=(",", ":")) if errors_result else "",
            version,
        )
        # NOTE: diagnostics is NOT freed here — callers manage cleanup

    @property
    def info(self) -> DDWaf_info:
        return self._info

    def update_rules(
        self, removals: Sequence[tuple[str, str]], updates: Sequence[tuple[str, str, PayloadType]]
    ) -> bool:
        """update the rules of the WAF instance. return False if an error occurs."""
        ok = True
        for product, path in removals:
            ok &= py_remove_config(self._builder, path)
            self._rc_products.get(product, set()).discard(path)
            if product == "ASM_DD":
                self._asm_dd_cache.discard(path)
        for product, path, rules in updates:
            if product not in self._rc_products:
                self._rc_products[product] = set()
            self._rc_products[product].add(path)
            if product == "ASM_DD":
                if ASM_DD_DEFAULT in self._asm_dd_cache:
                    ok &= py_remove_config(self._builder, ASM_DD_DEFAULT)
                    self._asm_dd_cache.discard(ASM_DD_DEFAULT)
                self._asm_dd_cache.add(path)
            diagnostics = ddwaf_object()
            ruleset_object = ddwaf_object.create_without_limits(rules)
            ok &= py_add_or_update_config(self._builder, path, ruleset_object, diagnostics)
            self._set_info(diagnostics, "update")
            ddwaf_object_destroy(ruleset_object, DEFAULT_ALLOCATOR)
            ddwaf_object_destroy(diagnostics, DEFAULT_ALLOCATOR)
        if not self._asm_dd_cache:
            diagnostics = ddwaf_object()
            ok &= py_add_or_update_config(self._builder, ASM_DD_DEFAULT, self._default_ruleset, diagnostics)
            self._set_info(diagnostics, "update")
            ddwaf_object_destroy(diagnostics, DEFAULT_ALLOCATOR)
            self._asm_dd_cache.add(ASM_DD_DEFAULT)
        new_handle = py_ddwaf_builder_build_instance(self._builder)
        self._rc_products_str = ",".join(f"{p}:{len(v)}" for p, v in sorted(self._rc_products.items()) if v)
        if new_handle:
            self._handle = new_handle
            self._rc_updates += 1
        return ok

    def _at_request_start(self) -> Optional[ddwaf_context_capsule]:
        ctx = None
        if self._handle:
            self._lifespan += 1
            ctx = py_ddwaf_context_init(self._handle)
            ctx.rc_products = f"[{self._rc_products_str}] u:{self._rc_updates} r:{self._lifespan}"
        if not ctx:
            LOGGER.debug("DDWaf._at_request_start: failure to create the context.")
        return ctx

    def eval(
        self,
        ctx: ddwaf_context_capsule,
        data: DDWafRulesType,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> DDWaf_result:
        """Evaluate persistent data on the context (ddwaf_context_eval)."""
        start = time.monotonic()
        if not ctx:
            LOGGER.debug("DDWaf.eval: dry run. no context created.")
            return DDWaf_result(0, [], {}, 0, (time.monotonic() - start) * 1e6, False, self.empty_observator, {})

        result_obj = ddwaf_object()
        observator = _observator()
        wrapper = ddwaf_object(data, observator=observator)
        with ctx._lock:
            error = ddwaf_context_eval(ctx.ctx, wrapper, DEFAULT_ALLOCATOR, result_obj, int(timeout_ms * 1000))
        return self._process_result(error, result_obj, observator, start)

    def eval_ephemeral(
        self,
        ctx: ddwaf_context_capsule,
        data: DDWafRulesType,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> DDWaf_result:
        """Evaluate ephemeral data via a subcontext.

        Creates a subcontext from the parent context, evaluates ephemeral data on it,
        and destroys it. The data does not persist beyond this call.
        """
        start = time.monotonic()
        if not ctx:
            LOGGER.debug("DDWaf.eval_ephemeral: dry run. no context created.")
            return DDWaf_result(0, [], {}, 0, (time.monotonic() - start) * 1e6, False, self.empty_observator, {})

        result_obj = ddwaf_object()
        observator = _observator()
        wrapper = ddwaf_object(data, observator=observator)
        subctx = py_ddwaf_subcontext_init(ctx)
        try:
            with ctx._lock:
                error = ddwaf_subcontext_eval(
                    subctx.subctx, wrapper, DEFAULT_ALLOCATOR, result_obj, int(timeout_ms * 1000)
                )
        finally:
            del subctx  # triggers ddwaf_subcontext_destroy via capsule __del__
        return self._process_result(error, result_obj, observator, start)

    def _process_result(
        self,
        error: int,
        result_obj: ddwaf_object,
        observator: _observator,
        start: float,
    ) -> DDWaf_result:
        """Convert a raw ddwaf_context_eval / ddwaf_subcontext_eval result into DDWaf_result."""
        if error < 0:
            LOGGER.debug("DDWAF eval error: %d\nerror %s", error, self.info.errors)
        result = result_obj.struct
        if error == DDWAF_ERR_INTERNAL or not isinstance(result, dict):
            ddwaf_object_destroy(result_obj, DEFAULT_ALLOCATOR)
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
        ddwaf_object_destroy(result_obj, DEFAULT_ALLOCATOR)
        return main_res

    @property
    def initialized(self) -> bool:
        return bool(self._handle)

    def __del__(self) -> None:
        if hasattr(self, "_default_ruleset"):
            ddwaf_object_destroy(self._default_ruleset, DEFAULT_ALLOCATOR)
