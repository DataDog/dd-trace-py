"""Plugin interface for tracing integrations (POC).

Generalizes ddtrace._monkey's PATCH_MODULES-driven activation so a migrated integration can own its
own ModuleWatchdog hooks (module-level `enable()`/`disable()`), instead of a central dict driving a
single wrapt.importer.when_imported hook on its behalf. See the "Plugin Interface for Tracing
Integrations" RFC.

POC scope: this module provides the plugin protocol and an entry-point-backed registry
(`IntegrationRegistry`) for discovering migrated integrations, which also owns their version-gated
activation (`registry.enable_plugin()`/`registry.disable_plugin()`). `ddtrace._monkey`'s
`_patch_all()`/`patch()` now consult this registry for any contrib name it resolves to a plugin --
currently just `urllib3` -- and call `registry.enable_plugin(plugin)` (not `plugin.enable()`
directly -- see that method's own docstring for why) instead of going through the legacy
`when_imported`/`_on_import_factory` path for it; every other, un-migrated integration is untouched
and still goes through `PATCH_MODULES`/`_MODULES_FOR_CONTRIB` exactly as before. Generalizing this
from a single special-cased plugin lookup into a real `LegacyPluginAdapter`-based unification (RFC
Migration Plan, item 2) is still future work; what's here is enough for a migrated integration to be
activated for real through `ddtrace.auto`/`ddtrace-run`, not just callable directly.
"""

from functools import cached_property
from importlib.metadata import entry_points
from types import ModuleType
import typing as t
from typing import Union

from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import get_version_for_package
from ddtrace.internal.settings._config import config as _global_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.vendor.packaging.specifiers import SpecifierSet
from ddtrace.vendor.packaging.version import Version


log = get_logger(__name__)

ENTRY_POINT_GROUP = "ddtrace.integrations"

# Mirrors ddtrace.internal.products._TRUSTED_PRODUCT_DISTRIBUTIONS / _TRUSTED_PRODUCT_MODULE_PREFIXES.
# Duplicated rather than imported to avoid coupling this module to the (unrelated) products.py; a
# shared helper is worth extracting if/when both are wired into the same startup path.
_TRUSTED_INTEGRATION_DISTRIBUTIONS = frozenset({"ddtrace"})
_TRUSTED_INTEGRATION_MODULE_PREFIXES = frozenset({"ddtrace."})


class IntegrationException(Exception):
    """Base exception for the integration-plugin machinery -- not just patching: version
    compatibility checks, entry-point discovery, and activation can all raise one of these.
    """

    pass


class ModuleNotFoundException(IntegrationException):
    pass


class IncompatibleModuleException(IntegrationException):
    def __init__(self, message: str, installed_version: t.Optional[str] = None):
        super().__init__(message)
        self.installed_version = installed_version


def is_version_compatible(version: str, supported_versions_spec: str) -> bool:
    "Returns whether a given package version is compatible with the integration's supported version range."

    if not supported_versions_spec:
        return False

    if supported_versions_spec == "*":
        return True

    try:
        specifier_set = SpecifierSet(supported_versions_spec)
        return Version(version) in specifier_set
    except Exception:
        return False


def _get_installed_module_version(imported_module: ModuleType, hooked_module_name: str) -> Union[str, None]:
    "Returns the installed version of a module."

    if hasattr(imported_module, "get_versions"):
        return t.cast(Union[str, None], imported_module.get_versions().get(hooked_module_name))
    elif hasattr(imported_module, "get_version"):
        return t.cast(Union[str, None], imported_module.get_version())
    return None


def _get_integration_supported_versions(
    integration_patch_module: ModuleType, integration_name: str, hooked_module_name: str
) -> Union[str, None]:
    "Returns the supported version range for an integration."
    if not hasattr(integration_patch_module, "_supported_versions"):
        return None

    supported_versions = integration_patch_module._supported_versions()
    if hooked_module_name in supported_versions:
        return t.cast(Union[str, None], supported_versions[hooked_module_name])
    elif integration_name in supported_versions:
        return t.cast(Union[str, None], supported_versions[integration_name])
    return None


def check_module_compatibility(
    integration_patch_module: ModuleType, integration_name: str, hooked_module_name: str
) -> None:
    "Determines if a module should be patched based on installed version and the integration's supported version range."

    # stdlib modules will not have an associated version and should always be patched
    installed_version = _get_installed_module_version(integration_patch_module, hooked_module_name)
    if not installed_version:
        return

    supported_version_spec = _get_integration_supported_versions(
        integration_patch_module, integration_name, hooked_module_name
    )
    if not supported_version_spec:
        # TODO: once all integrations have a supported version spec, we should raise an error here
        return

    if not is_version_compatible(installed_version, supported_version_spec):
        message = (
            f"Skipped patching '{integration_name}' integration, installed version: {installed_version} "
            f"is not compatible with integration support spec: {supported_version_spec}."
        )
        raise IncompatibleModuleException(message, installed_version=installed_version)
    return


class IntegrationPlugin(t.Protocol):
    """Structural protocol a contrib patch.py module satisfies via module-level names -- not a base
    class anything inherits from or instantiates, the same shape ddtrace.internal.products.Product
    already uses for products.

    `supported_versions` is part of this protocol, not an ad-hoc convention a generic helper
    discovers via `getattr`/`hasattr`: it's a dict of *distribution* name (as installed, e.g. via
    PyPI -- not necessarily the same as any module name it ships) to version specifier, exactly
    like `name`/`default_enabled`. Reading it, and checking it, never imports anything:
    `enable_plugin()` (below) resolves each entry's installed version from distribution metadata
    (`ddtrace.internal.packages.get_version_for_package()`, ultimately `importlib.metadata`), which
    is available the moment the package is installed, whether or not it's ever imported -- unlike a
    module attribute such as `__version__`, which isn't a guaranteed convention and requires the
    module to already be imported to read at all. There's no `get_version()` method on this
    protocol: nothing in the machinery needs one, since `enable_plugin()` reads installed versions
    straight from `get_version_for_package()` against `supported_versions`' own keys.
    """

    name: str
    default_enabled: bool
    requires: tuple[str, ...]
    supported_versions: dict[str, str]

    def enable(self) -> None: ...

    def disable(self) -> None: ...


def _get_integration_entry_points() -> list[t.Any]:
    if is_at_least_py(3, 10):
        return list(entry_points(group=ENTRY_POINT_GROUP))
    return [ep for _, eps in entry_points().items() for ep in eps if ep.group == ENTRY_POINT_GROUP]


class IntegrationRegistry:
    """Discovers migrated integrations via the `ddtrace.integrations` entry-point group, the same
    way ddtrace.internal.products.ProductManager discovers products. Consulted by ddtrace._monkey's
    `_patch_all()`/`patch()` (see module docstring) to activate migrated plugins for real, alongside
    every un-migrated integration's unchanged legacy path.

    Also owns which integration_names are currently enabled (`_enabled`) -- not module-level state,
    since that state has no meaning independent of a specific registry: a plugin never tracks its
    own "am I patched" state (no local flag, no is_module_patched()-style helper), and this
    registry's own `enable_plugin()`/`disable_plugin()` are the sole entry points that ever call a
    plugin's `enable()`/`disable()`, so it -- not the plugin, and not some free-floating module
    global -- is the thing that knows whether a given plugin is already running, and can enforce
    that `enable()`/`disable()` each only ever do real work once per on/off cycle.
    """

    def __init__(self) -> None:
        self._enabled: set[str] = set()

    def _load(self) -> dict[str, ModuleType]:
        plugins: dict[str, ModuleType] = {}
        for entry_point in _get_integration_entry_points():
            module_path = entry_point.value.partition(":")[0]
            dist = getattr(entry_point, "dist", None)
            if not hasattr(entry_point, "dist") or dist is None:
                trusted_dist = True
            else:
                dist_name = dist.metadata["Name"]
                trusted_dist = dist_name is not None and dist_name.lower() in _TRUSTED_INTEGRATION_DISTRIBUTIONS
            trusted_module = any(module_path.startswith(p) for p in _TRUSTED_INTEGRATION_MODULE_PREFIXES)
            if not (trusted_dist and trusted_module):
                log.warning(
                    "Refusing to load integration plugin '%s' from untrusted distribution (module: %s)",
                    entry_point.name,
                    module_path,
                )
                continue

            try:
                plugin = entry_point.load()
            except Exception:
                log.exception("Failed to load integration plugin '%s'", entry_point.name)
                continue

            # No explicit config registration needed here: entry_point.load() just imported the
            # plugin's patch.py, which (per convention) constructs its own module-level `config` as
            # part of that same import -- and IntegrationEnvConfig.__init__ (ddtrace/internal/
            # settings/integration.py) self-registers into config.<name> at construction time. See
            # that module's docstring for why this is pull-based rather than something this registry
            # has to do as a side effect of discovery.
            plugins[entry_point.name] = plugin

        return plugins

    @cached_property
    def _plugins(self) -> dict[str, ModuleType]:
        return self._load()

    def __iter__(self) -> t.Iterator[ModuleType]:
        return iter(self._plugins.values())

    def get(self, name: str) -> t.Optional[ModuleType]:
        return self._plugins.get(name)

    def enable_plugin(self, plugin: ModuleType) -> None:
        """
        Activates `plugin` -- the only thing ever allowed to call
        `plugin.enable()`, and a no-op if `plugin.name` is already enabled.
        Checks `plugin.supported_versions` (distribution name -> version
        specifier) against each distribution's installed version via
        `get_version_for_package()`, never by importing anything. Reports
        failure telemetry and skips `enable()` if any entry is incompatible; a
        distribution with no installed version is treated as compatible.
        Otherwise reports "enabled" telemetry, reports the plugin's own config
        (if it has one) via `report_configuration()` -- the same call
        `ProductManager` makes for a product's own config, just never wired up
        for integrations until now -- calls `enable()`, and records
        `plugin.name` as enabled.
        """
        integration_name = plugin.name
        if integration_name in self._enabled:
            return

        supported_versions: dict[str, str] = getattr(plugin, "supported_versions", {})

        version = ""
        for dist_name, spec in supported_versions.items():
            version = get_version_for_package(dist_name)
            if version and spec and not is_version_compatible(version, spec):
                message = (
                    f"Skipped enabling '{integration_name}' integration, installed version: {version} "
                    f"is not compatible with integration support spec: {spec}."
                )
                log.error(
                    "failed to enable ddtrace support for %s: %s",
                    integration_name,
                    message,
                    extra={"send_to_telemetry": False},
                )
                telemetry_writer.add_integration(integration_name, False, True, message, version=version)
                return

        telemetry_writer.add_integration(integration_name, True, True, "", version=version)

        plugin_config = getattr(_global_config, integration_name, None)
        if isinstance(plugin_config, DDConfig):
            report_configuration(plugin_config)

        plugin.enable()
        self._enabled.add(integration_name)

    def disable_plugin(self, plugin: ModuleType) -> None:
        """
        Deactivates `plugin` -- the counterpart to `enable_plugin()`, and the
        only thing that's ever allowed to call `plugin.disable()`. A no-op if
        `plugin.name` isn't currently enabled, so `disable()` is idempotent the
        same way `enable()` is. Clears the enabled record so a later
        `enable_plugin()` call for the same plugin genuinely re-activates it.
        """
        integration_name = plugin.name
        if integration_name not in self._enabled:
            return

        plugin.disable()
        self._enabled.discard(integration_name)


registry = IntegrationRegistry()
