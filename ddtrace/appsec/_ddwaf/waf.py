import json
import time
from typing import Any
from typing import Optional
from typing import Sequence
from typing import Union

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf.ddwaf_types import DEFAULT_ALLOCATOR
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_context_eval
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_object_free
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
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_subcontext_capsule
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
OBFUSCATOR_CONFIG = "obfuscator/config"


class DDWaf:
    empty_observator = _observator()

    def __init__(
        self,
        ruleset_json_str: bytes,
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ) -> None:
        diagnostics = ddwaf_object()
        ruleset_map_object = ddwaf_object.from_json_bytes(ruleset_json_str)
        if not ruleset_map_object:
            raise ValueError("Invalid ruleset provided to DDWaf constructor")
        self._builder = py_ddwaf_builder_init()
        # libddwaf 2.0 has no ddwaf_config: obfuscator regexes are a builder config. Both keys are
        # optional (defaults apply when omitted), so only add it when a regex is set. The builder
        # copies the config, so the source can be freed right after.
        obfuscator = {}
        if obfuscation_parameter_key_regexp:
            obfuscator["key_regex"] = obfuscation_parameter_key_regexp
        if obfuscation_parameter_value_regexp:
            obfuscator["value_regex"] = obfuscation_parameter_value_regexp
        if obfuscator:
            obfuscator_config = ddwaf_object.create_without_limits({"obfuscator": obfuscator})
            obfuscator_diagnostics = ddwaf_object()
            py_add_or_update_config(self._builder, OBFUSCATOR_CONFIG, obfuscator_config, obfuscator_diagnostics)
            ddwaf_object_free(obfuscator_diagnostics)
            ddwaf_object_free(obfuscator_config)
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
        ddwaf_object_free(diagnostics)

    @property
    def info(self) -> DDWaf_info:
        return self._info

    def update_rules(
        self, removals: Sequence[tuple[str, str]], updates: Sequence[tuple[str, str, PayloadType]]
    ) -> bool:
        """update the rules of the WAF instance. return False if an error occurs."""
        ok = True
        for product, path in removals:
            py_remove_config(self._builder, path)
            self._rc_products.get(product, set()).discard(path)
            if product == "ASM_DD":
                self._asm_dd_cache.discard(path)
        for product, path, rules in updates:
            if product not in self._rc_products:
                self._rc_products[product] = set()
            self._rc_products[product].add(path)
            if product == "ASM_DD":
                if ASM_DD_DEFAULT in self._asm_dd_cache:
                    # we need to remove the default ruleset before adding the new one
                    ok &= py_remove_config(self._builder, ASM_DD_DEFAULT)
                    self._asm_dd_cache.discard(ASM_DD_DEFAULT)
                self._asm_dd_cache.add(path)
            diagnostics = ddwaf_object()
            ruleset_object = ddwaf_object.create_without_limits(rules)
            res = py_add_or_update_config(self._builder, path, ruleset_object, diagnostics)
            self._set_info(diagnostics, "update")
            ddwaf_object_free(ruleset_object)
            ok &= res
            if not res:
                self._rc_products[product].discard(path)
                if product == "ASM_DD":
                    self._asm_dd_cache.discard(path)
        if not self._asm_dd_cache:
            # we need to add the default ruleset back
            diagnostics = ddwaf_object()
            ok &= py_add_or_update_config(self._builder, ASM_DD_DEFAULT, self._default_ruleset, diagnostics)
            self._set_info(diagnostics, "update")
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

    def new_subcontext(self, ctx: ddwaf_context_capsule) -> Optional[ddwaf_subcontext_capsule]:
        """Create a fresh subcontext from the per-request main context.

        Subcontexts are libddwaf 2.0's replacement for ephemeral data: they inherit the
        context's persistent data but evaluate non-persisting data (used for RASP, one
        subcontext per guarded operation). Subcontexts may then be evaluated concurrently with
        each other and with the parent context (each capsule has its own lock), but creating one
        derives from the parent, so we serialize init under the parent context lock.
        """
        if not ctx:
            return None
        with ctx._lock:
            subctx = py_ddwaf_subcontext_init(ctx)
        if not subctx:
            LOGGER.debug("DDWaf.new_subcontext: failure to create the subcontext.")
            return None
        return subctx

    def run(
        self,
        target: Union[ddwaf_context_capsule, ddwaf_subcontext_capsule],
        data: DDWafRulesType,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> DDWaf_result:
        # Single data object per call; persistence depends on the eval target chosen by the caller
        # (main context for request data, subcontext for non-persisting RASP data).
        start = time.monotonic()
        if not target:
            LOGGER.debug("DDWaf.run: dry run. no context created.")
            return DDWaf_result(0, [], {}, 0, (time.monotonic() - start) * 1e6, False, self.empty_observator, {})

        result_obj = ddwaf_object()
        observator = _observator()
        wrapper = ddwaf_object(data, observator=observator)
        if isinstance(target, ddwaf_subcontext_capsule):
            handle, eval_fn = target.subctx, ddwaf_subcontext_eval
        else:
            handle, eval_fn = target.ctx, ddwaf_context_eval
        with target._lock:
            error = eval_fn(handle, wrapper, DEFAULT_ALLOCATOR, result_obj, int(timeout_ms * 1000))
        # Input ownership after eval (ddwaf.h): OK/MATCH -> context owns it (freed on context
        # destroy); INVALID_OBJECT(-2) -> libddwaf already freed it (don't read/free);
        # INVALID_ARGUMENT(-1) -> not taken, free here; INTERNAL(-3) -> undefined, leave alone.
        if error < 0:
            # Log the source dict, never wrapper.struct (the wrapper may already be freed).
            LOGGER.debug("run DDWAF error: %d\ninput %s\nerror %s", error, data, self.info.errors)
            if error == DDWAF_ERR_INVALID_ARGUMENT:
                ddwaf_object_free(wrapper)
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

    def __del__(self) -> None:
        if hasattr(self, "_default_ruleset"):
            ddwaf_object_free(self._default_ruleset)
