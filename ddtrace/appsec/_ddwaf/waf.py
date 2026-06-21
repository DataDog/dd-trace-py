import json
import time
from typing import Any
from typing import Optional
from typing import Sequence
from typing import Union

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf.ddwaf_types import DDWAF_MAX_CONTAINER_DEPTH
from ddtrace.appsec._ddwaf.ddwaf_types import DDWAF_MAX_CONTAINER_SIZE
from ddtrace.appsec._ddwaf.ddwaf_types import DDWAF_MAX_STRING_LENGTH
from ddtrace.appsec._ddwaf.ddwaf_types import py_add_or_update_config
from ddtrace.appsec._ddwaf.ddwaf_types import py_add_or_update_config_json
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


def _run_eval(native: Any, data: DDWafRulesType, timeout_us: int) -> Any:
    """Evaluate ``data`` against a native context/subcontext, returning the native ``WafResult``.

    Factored out as a small module-level seam so it can be patched in tests.
    """
    return native.run(data, DDWAF_MAX_CONTAINER_SIZE, DDWAF_MAX_CONTAINER_DEPTH, DDWAF_MAX_STRING_LENGTH, timeout_us)


class DDWaf:
    empty_observator = _observator()

    def __init__(
        self,
        ruleset_json_str: bytes,
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ) -> None:
        # libddwaf 2.0 has no ddwaf_config: the obfuscator regexes are applied as the builder's base
        # config (both keys are optional, defaults apply when empty).
        self._builder = py_ddwaf_builder_init(obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp)
        # Keep the raw JSON and load it through libddwaf's native parser (faster than deserializing
        # to Python objects and re-marshalling); reused as-is when the default ruleset is re-added.
        self._default_ruleset = ruleset_json_str
        try:
            _, diagnostics = py_add_or_update_config_json(self._builder, ASM_DD_DEFAULT, ruleset_json_str)
        except Exception:
            raise ValueError("Invalid ruleset provided to DDWaf constructor")
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
                ruleset_json_str,
                info.failed,
                info.errors,
            )
        self._rc_products: dict[str, set[str]] = {}
        self._rc_products_str: str = ""
        self._rc_updates: int = 0
        self._lifespan: int = 0

    @property
    def required_data(self) -> list[str]:
        return py_ddwaf_known_addresses(self._handle) if self._handle else []

    def _set_info(self, diagnostics: dict, action: str) -> None:
        info_struct: dict[str, Any] = diagnostics or {}
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
                    # we need to remove the default ruleset before adding the new one
                    ok &= py_remove_config(self._builder, ASM_DD_DEFAULT)
                    self._asm_dd_cache.discard(ASM_DD_DEFAULT)
                self._asm_dd_cache.add(path)
            ok_update, diagnostics = py_add_or_update_config(self._builder, path, rules)
            ok &= ok_update
            self._set_info(diagnostics, "update")
        if not self._asm_dd_cache:
            # we need to add the default ruleset back (kept as raw JSON, parsed natively)
            ok_update, diagnostics = py_add_or_update_config_json(self._builder, ASM_DD_DEFAULT, self._default_ruleset)
            ok &= ok_update
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

        native = target.subctx if isinstance(target, ddwaf_subcontext_capsule) else target.ctx
        # Building the input and evaluating it (with the GIL released) both happen inside the native
        # run; input ownership and error-code handling are managed by the libddwaf crate.
        with target._lock:
            result = _run_eval(native, data, int(timeout_ms * 1000))
        total_runtime = (time.monotonic() - start) * 1e6
        if result.return_code < 0:
            LOGGER.debug("run DDWAF error: %d\ninput %s\nerror %s", result.return_code, data, self.info.errors)
            return DDWaf_result(result.return_code, [], {}, 0, total_runtime, False, self.empty_observator, {})
        observator = _observator()
        observator.string_length, observator.container_size, observator.container_depth = result.truncation
        return DDWaf_result(
            result.return_code,
            result.events,
            result.actions,
            result.duration,
            total_runtime,
            result.timeout,
            observator,
            result.attributes,
            result.keep,
        )

    @property
    def initialized(self) -> bool:
        return bool(self._handle)
