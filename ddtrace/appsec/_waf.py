"""Tracer configuration policy around the native WAF builder.

The builder owns mutable configuration; each built engine is an immutable
snapshot. Request contexts keep their engine alive across configuration updates.
"""

from collections.abc import Sequence
import json
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._metrics import report_error
from ddtrace.appsec._waf_types import WafInput
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ddwaf as native
from ddtrace.internal.remoteconfig import PayloadType


LOGGER = get_logger(__name__)
ASM_DD_DEFAULT = "ASM_DD/default"
OBFUSCATOR_CONFIG = "obfuscator/config"


class DDWafContext:
    def __init__(self, context: native.Context, rc_products: str = "") -> None:
        self.native = context
        self.rc_products = rc_products

    def run(self, data: WafInput, timeout_us: int) -> native.Result:
        return self.native.run(data, timeout_us=timeout_us, compatibility=True)


class DDWaf(native.Builder):
    def __new__(
        cls,
        ruleset_json_str: bytes,
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ) -> "DDWaf":
        return super().__new__(cls)

    def __init__(
        self,
        ruleset_json_str: bytes,
        obfuscation_parameter_key_regexp: bytes,
        obfuscation_parameter_value_regexp: bytes,
    ) -> None:
        self._fork_generation = forksafe.get_generation()
        self._obfuscation = (obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp)
        self._configs: dict[tuple[str, str], bytes] = {}
        # Publish immutable replay inputs with each engine, including accepted partial configs.
        self._fork_updates: tuple[tuple[str, str, bytes], ...] = ()
        self._builder_lock = forksafe.Lock()
        self._default_ruleset = ruleset_json_str
        self._rc_products: dict[str, set[str]] = {}
        self._using_default = True
        self._rc_products_str = ""
        self._rc_updates = 0
        self._lifespan = 0
        self._cached_version = ""
        obfuscator = {}
        if obfuscation_parameter_key_regexp:
            obfuscator["key_regex"] = obfuscation_parameter_key_regexp
        if obfuscation_parameter_value_regexp:
            obfuscator["value_regex"] = obfuscation_parameter_value_regexp
        if obfuscator:
            self.add_config(OBFUSCATOR_CONFIG, {"obfuscator": obfuscator})
        _, diagnostics = self.add_config(ASM_DD_DEFAULT, ruleset_json_str)
        self._set_info(diagnostics, "init")
        self._handle = self.build()

    @property
    def needs_rebuild(self) -> bool:
        return self._fork_generation != forksafe.get_generation()

    def fork_clone(self) -> "DDWaf":
        """Rebuild outside the at-fork hook, without touching inherited native state."""
        fresh = type(self)(self._default_ruleset, *self._obfuscation)
        if self._fork_updates:
            fresh.update_rules([], self._fork_updates)
        fresh._rc_updates = self._rc_updates
        return fresh

    @property
    def required_data(self) -> list[str]:
        return self._handle.required_data if self._handle is not None else []

    @property
    def initialized(self) -> bool:
        return self._handle is not None

    @property
    def info(self) -> native.RulesetInfo:
        return self._info

    def _set_info(self, diagnostics: dict[str, Any], action: str) -> None:
        diagnostics = diagnostics or {}
        rules = diagnostics.get("rules", {})
        self._cached_version = diagnostics.get("ruleset_version", self._cached_version)
        for key, value in diagnostics.items():
            if isinstance(value, dict):
                if error := value.get("error"):
                    report_error(f"appsec.waf.error::{action}::{key}::{error}", self._cached_version, action)
                elif errors := value.get("errors"):
                    report_error(f"appsec.waf.error::{action}::{key}::{errors}", self._cached_version, action, False)
        errors = rules.get("errors", {})
        self._info = native.RulesetInfo(
            version=self._cached_version,
            accepted_rules=len(rules.get("loaded", [])),
            rejected_rules=len(rules.get("failed", [])),
            errors=errors,
        )

    def update_rules(
        self,
        removals: Sequence[tuple[str, str]],
        updates: Sequence[tuple[str, str, Union[PayloadType, bytes]]],
    ) -> bool:
        with self._builder_lock:
            success = True
            for product, path in removals:
                self.remove_config(path)
                self._configs.pop((product, path), None)
                self._rc_products.get(product, set()).discard(path)
            for product, path, rules in updates:
                try:
                    serialized = rules if isinstance(rules, bytes) else json.dumps(rules, allow_nan=False).encode()
                except (TypeError, ValueError):
                    LOGGER.debug("Invalid WAF configuration at %s", path, exc_info=True)
                    success = False
                    continue
                if product == "ASM_DD" and self._using_default:
                    self.remove_config(ASM_DD_DEFAULT)
                    self._using_default = False
                try:
                    accepted, diagnostics = self.add_config(path, serialized)
                except ValueError as error:
                    # Malformed JSON must restore the default just like rejected rules.
                    self.remove_config(path)
                    accepted, diagnostics = False, {"rules": {"error": str(error)}}
                self._set_info(diagnostics, "update")
                success &= accepted
                if accepted:
                    self._rc_products.setdefault(product, set()).add(path)
                    self._configs[product, path] = serialized
                else:
                    self._rc_products.get(product, set()).discard(path)
                    self._configs.pop((product, path), None)
            if not self._rc_products.get("ASM_DD") and not self._using_default:
                restored, diagnostics = self.add_config(ASM_DD_DEFAULT, self._default_ruleset)
                success &= restored
                self._set_info(diagnostics, "update")
                self._using_default = restored
            handle = self.build()
            self._rc_products_str = ",".join(
                f"{p}:{len(paths)}" for p, paths in sorted(self._rc_products.items()) if paths
            )
            if handle is not None:
                self._handle = handle
                self._fork_updates = tuple((p, path, rules) for (p, path), rules in self._configs.items())
                self._rc_updates += 1
            return bool(success)

    def _at_request_start(self) -> Optional[DDWafContext]:
        if self._handle is None:
            return None
        self._lifespan += 1
        products = f"[{self._rc_products_str}] u:{self._rc_updates} r:{self._lifespan}"
        return DDWafContext(self._handle.context(), products)

    def new_subcontext(self, context: Optional[DDWafContext]) -> Optional[DDWafContext]:
        if context is None:
            return None
        return DDWafContext(context.native.subcontext())

    def run(
        self,
        target: Optional[DDWafContext],
        data: WafInput,
        timeout_ms: float = DEFAULT.WAF_TIMEOUT,
    ) -> native.Result:
        if target is None:
            return native.Result()
        try:
            return target.run(data, timeout_us=int(timeout_ms * 1000)).prepare()
        except native.EvaluationError as error:
            LOGGER.debug("run DDWAF error: %s", error)
            return native.Result(error_code=error.args[0])
