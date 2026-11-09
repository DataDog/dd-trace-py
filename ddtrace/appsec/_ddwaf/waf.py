"""libddwaf backend implemented by the native extension (``ddtrace.internal.native._native.ddwaf``).

Python keeps the configuration policy (default ruleset, remote config bookkeeping, diagnostics
reporting); conversion, evaluation, result decoding and native lifetimes live in Rust. Importing
this module loads libddwaf and raises if it cannot be loaded, which disables AppSec.
"""

from collections.abc import Sequence
import json
from platform import system
import time
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._ddwaf._types import DDWafInputType
from ddtrace.appsec._metrics import report_error
from ddtrace.appsec._utils import DDWaf_info
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _observator
from ddtrace.internal import _libddwaf_platform
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ddwaf as _native
from ddtrace.internal.remoteconfig import PayloadType
from ddtrace.internal.settings.asm import config as asm_config


LOGGER = get_logger(__name__)

DDWAF_ERR_INTERNAL = -3

ASM_DD_DEFAULT = "ASM_DD/default"
OBFUSCATOR_CONFIG = "obfuscator/config"

asm_config._asm_libddwaf, asm_config._ddwaf_version = _native.load(
    list(_libddwaf_platform.load_candidates(asm_config._asm_libddwaf, system()))
)


class DDWafContext:
    """Per-request evaluation target. ``run`` is the native context's own method."""

    __slots__ = ("native", "run", "rc_products")

    def __init__(self, native: _native.Context, rc_products: str = "") -> None:
        self.native = native
        self.run = native.run
        self.rc_products = rc_products


DDWafSubContext = DDWafContext


def py_ddwaf_builder_get_config_paths(builder: _native.Builder, filter_str: str) -> int:
    return builder.config_paths_count(filter_str)


class DDWaf:
    empty_observator = _observator()

    def __init__(
        self,
        ruleset_json_str: bytes,
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ) -> None:
        self._builder = _native.Builder()
        self._builder_lock = forksafe.Lock()
        # Both obfuscator keys are optional (defaults apply when omitted).
        obfuscator = {}
        if obfuscation_parameter_key_regexp:
            obfuscator["key_regex"] = obfuscation_parameter_key_regexp
        if obfuscation_parameter_value_regexp:
            obfuscator["value_regex"] = obfuscation_parameter_value_regexp
        if obfuscator:
            self._builder.add_or_update_config(OBFUSCATOR_CONFIG, {"obfuscator": obfuscator})
        try:
            _, diagnostics = self._builder.add_or_update_config(ASM_DD_DEFAULT, ruleset_json_str)
        except ValueError:
            raise ValueError("Invalid ruleset provided to DDWaf constructor")
        self._handle = self._builder.build()
        self._cached_version = ""
        self._asm_dd_cache = {ASM_DD_DEFAULT}
        self._set_info(diagnostics, "init")
        info = self.info
        if self._handle is None or info.failed:
            # Keep the handle: some valid rules can load while invalid ones are rejected.
            LOGGER.debug("DDWAF.__init__: invalid rules\nloaded:%s\nerrors:%s\n", info.failed, info.errors)
        self._default_ruleset = ruleset_json_str
        self._rc_products: dict[str, set[str]] = {}
        self._rc_products_str: str = ""
        self._rc_updates: int = 0
        self._lifespan: int = 0

    @property
    def required_data(self) -> list[str]:
        return self._handle.known_addresses() if self._handle is not None else []

    def _set_info(self, diagnostics: Any, action: str) -> None:
        info_struct: dict[str, Any] = diagnostics if isinstance(diagnostics, dict) else {}
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
        # libddwaf requires external synchronization for builder access; keep native operations
        # and their Python-side bookkeeping in the same critical section.
        with self._builder_lock:
            builder = self._builder
            ok = True
            for product, path in removals:
                builder.remove_config(path)
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
                        ok &= builder.remove_config(ASM_DD_DEFAULT)
                        self._asm_dd_cache.discard(ASM_DD_DEFAULT)
                    self._asm_dd_cache.add(path)
                res, diagnostics = builder.add_or_update_config(path, rules)
                self._set_info(diagnostics, "update")
                ok &= res
                if not res:
                    self._rc_products[product].discard(path)
                    if product == "ASM_DD":
                        self._asm_dd_cache.discard(path)
            if not self._asm_dd_cache:
                # we need to add the default ruleset back
                res, diagnostics = builder.add_or_update_config(ASM_DD_DEFAULT, self._default_ruleset)
                ok &= res
                self._set_info(diagnostics, "update")
                self._asm_dd_cache.add(ASM_DD_DEFAULT)
            new_handle = builder.build()
            self._rc_products_str = ",".join(f"{p}:{len(v)}" for p, v in sorted(self._rc_products.items()) if v)
            if new_handle is not None:
                self._handle = new_handle
                self._rc_updates += 1
            return ok

    def _at_request_start(self) -> Optional[DDWafContext]:
        native = self._handle.context() if self._handle is not None else None
        if native is None:
            LOGGER.debug("DDWaf._at_request_start: failure to create the context.")
            return None
        self._lifespan += 1
        return DDWafContext(native, f"[{self._rc_products_str}] u:{self._rc_updates} r:{self._lifespan}")

    def new_subcontext(self, ctx: Optional[DDWafContext]) -> Optional[DDWafSubContext]:
        """Create a fresh subcontext from the per-request main context.

        Subcontexts inherit the context's persistent data but evaluate non-persisting data (RASP,
        one subcontext per guarded operation). Creation is serialized with the parent context.
        """
        if not ctx:
            return None
        native = ctx.native.subcontext()
        if native is None:
            LOGGER.debug("DDWaf.new_subcontext: failure to create the subcontext.")
            return None
        return DDWafContext(native)

    def run(
        self,
        target: Union[DDWafContext, DDWafSubContext],
        data: DDWafInputType,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> DDWaf_result:
        # Persistence depends on the target: main context for request data, subcontext for RASP.
        start = time.monotonic()
        if not target:
            LOGGER.debug("DDWaf.run: dry run. no context created.")
            return DDWaf_result(0, [], {}, 0, (time.monotonic() - start) * 1e6, False, self.empty_observator, {})

        error, events, actions, duration_ns, timeout, attributes, keep, s_len, c_size, c_depth = target.run(
            data, int(timeout_ms * 1000)
        )
        if error < 0:
            LOGGER.debug("run DDWAF error: %d\ninput %s\nerror %s", error, data, self.info.errors)
        if events is None or actions is None or attributes is None:
            # libddwaf produced no result (internal error or rejected input)
            return DDWaf_result(error, [], {}, 0, 0, False, self.empty_observator, {})
        if s_len is None and c_size is None and c_depth is None:
            observator = self.empty_observator
        else:
            observator = _observator()
            observator.string_length = s_len
            observator.container_size = c_size
            observator.container_depth = c_depth
        return DDWaf_result(
            error,
            events,
            actions,
            duration_ns / 1e3,
            (time.monotonic() - start) * 1e6,
            timeout,
            observator,
            attributes,
            keep,
        )

    @property
    def initialized(self) -> bool:
        return self._handle is not None
