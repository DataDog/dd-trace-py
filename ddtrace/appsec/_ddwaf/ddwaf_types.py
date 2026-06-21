from enum import IntEnum

from ddtrace.appsec._ddwaf.waf_stubs import DDWafRulesType
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_builder_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_context_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_handle_capsule
from ddtrace.appsec._ddwaf.waf_stubs import ddwaf_subcontext_capsule
from ddtrace.appsec._utils import _observator  # noqa: F401  (re-exported; imported by tests)
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ddwaf as _native
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

#
# Thin Python forwarders over the high-level native libddwaf bindings
# (``ddtrace.internal.native._native.ddwaf``). Object building/reading lives in Rust now; these
# helpers just wrap the native Builder/Handle/Context/Subcontext in the lifetime/lock capsules and
# pass the build limits through. libddwaf is statically embedded in the native extension.
#

#
# Build limits (forwarded to the native object builder). Kept here so the Python orchestration
# controls truncation; the native side caps containers at the uint16 ceiling regardless.
#

DDWAF_MAX_STRING_LENGTH = 4096
DDWAF_MAX_CONTAINER_DEPTH = 20
DDWAF_MAX_CONTAINER_SIZE = 256
DDWAF_NO_LIMIT = 1 << 31
DDWAF_DEPTH_NO_LIMIT = 1000

# Containers store their size/capacity in a uint16, so they cannot hold more than this.
DDWAF_OBJ_MAX_CAPACITY = 0xFFFF


class DDWAF_RET_CODE(IntEnum):
    DDWAF_ERR_INTERNAL = -3
    DDWAF_ERR_INVALID_OBJECT = -2
    DDWAF_ERR_INVALID_ARGUMENT = -1
    DDWAF_OK = 0
    DDWAF_MATCH = 1


def py_ddwaf_builder_init(obfuscation_key: bytes, obfuscation_value: bytes) -> ddwaf_builder_capsule:
    # Empty regexes mean "use libddwaf defaults" (None on the native side).
    return ddwaf_builder_capsule(_native.Builder(obfuscation_key or None, obfuscation_value or None))


def py_add_or_update_config(builder: ddwaf_builder_capsule, path: str, ruleset: DDWafRulesType) -> tuple[bool, dict]:
    """Add/update a config; returns (ok, diagnostics) where diagnostics is a Python dict."""
    return builder.builder.add_or_update_config(path, ruleset)


def py_add_or_update_config_json(builder: ddwaf_builder_capsule, path: str, ruleset_json: bytes) -> tuple[bool, dict]:
    """Add/update a config from JSON bytes (parsed natively by libddwaf, no Python intermediate).

    Raises ValueError on invalid JSON. Returns (ok, diagnostics) like ``py_add_or_update_config``.
    """
    return builder.builder.add_or_update_config_json(path, ruleset_json)


def py_remove_config(builder: ddwaf_builder_capsule, path: str) -> bool:
    return builder.builder.remove_config(path)


def py_ddwaf_builder_build_instance(builder: ddwaf_builder_capsule) -> ddwaf_handle_capsule:
    return ddwaf_handle_capsule(builder.builder.build_instance())


def py_ddwaf_builder_get_config_paths(builder: ddwaf_builder_capsule, filter_str: str = "") -> int:
    return builder.builder.config_paths_count(filter_str)


def py_ddwaf_known_addresses(handle: ddwaf_handle_capsule) -> list[str]:
    return handle.handle.known_addresses() if handle.handle else []


def py_ddwaf_context_init(handle: ddwaf_handle_capsule) -> ddwaf_context_capsule:
    return ddwaf_context_capsule(handle.handle.new_context() if handle.handle else None)


def py_ddwaf_subcontext_init(ctx: ddwaf_context_capsule) -> ddwaf_subcontext_capsule:
    return ddwaf_subcontext_capsule(ctx.ctx.new_subcontext() if ctx.ctx else None)


asm_config._ddwaf_version = _native.version()
