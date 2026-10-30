from importlib.metadata import entry_points
import typing as t

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# ddtrace's entry-point groups are internal-only. We reject plugins from any
# distribution other than ddtrace's own to prevent supply-chain attacks where a
# malicious third-party package registers a plugin that would be automatically
# loaded into every instrumented process.
# Distribution names are matched exactly (case-insensitively); module paths are
# matched as prefixes so all ddtrace.* submodules are covered.
# Serverless builds publish the same package under the "ddtrace-serverless"
# distribution name (see PACKAGE_NAME in setup.py).
_TRUSTED_DISTRIBUTIONS = frozenset({"ddtrace", "ddtrace-serverless"})
_TRUSTED_MODULE_PREFIXES = frozenset({"ddtrace."})


def get_entry_points(group: str) -> list[t.Any]:
    return list(entry_points(group=group))


def is_trusted(plugin: t.Any) -> bool:
    """Whether an entry point belongs to ddtrace itself, logging a warning if not."""
    # Check both the distribution name and the entry point module path. The
    # distribution name check is a fast first filter, but it is self-declared
    # and can be spoofed. The module path check is the stronger guard: a
    # plugin whose code does not live under the ddtrace namespace cannot be
    # part of the trusted ddtrace package.
    module_path, _, _ = plugin.value.partition(":")
    # dist may be absent on Python < 3.10 with the legacy entry_points() API;
    # in that case skip the dist check and rely solely on the module path. A
    # dist name that is explicitly None (malformed metadata) is treated as
    # untrusted.
    dist = getattr(plugin, "dist", None)
    # Three cases:
    #  - dist attribute absent (Python < 3.10): no dist info, skip dist check
    #  - dist is None: no dist info available, skip dist check
    #  - dist present and not None: check Name; None Name means malformed metadata → reject
    dist_name: t.Optional[str] = None
    if not hasattr(plugin, "dist") or dist is None:
        trusted_dist = True
    else:
        dist_name = dist.metadata["Name"]
        trusted_dist = dist_name is not None and dist_name.lower() in _TRUSTED_DISTRIBUTIONS
    trusted_module = any(module_path.startswith(p) for p in _TRUSTED_MODULE_PREFIXES)
    if not (trusted_dist and trusted_module):
        log.warning(
            "Refusing to load %s plugin '%s' from untrusted distribution '%s' (module: %s)",
            plugin.group,
            plugin.name,
            dist_name,
            module_path,
        )
        return False
    return True
