import sys
import types
from typing import Optional
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.internal.integrations import IntegrationRegistry
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.integration import IntegrationEnvConfig
from ddtrace.internal.settings.integration import _pending_plugin_configs


# --- IntegrationRegistry: entry-point discovery and trust filter (mirrors tests/internal/test_products.py) ---


class _FakePlugin:
    name = "fake"
    default_enabled = False


def _make_entry_point(name, dist_name, module_path):
    ep = MagicMock()
    ep.name = name
    ep.value = f"{module_path}:module"
    ep.dist = MagicMock()
    ep.dist.metadata = {"Name": dist_name}
    ep.load.return_value = _FakePlugin()
    return ep


def test_load_plugins_trusted():
    ep = _make_entry_point("fake", "ddtrace", "ddtrace.some.module")

    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations._get_integration_entry_points", return_value=[ep]):
        plugins = registry._load()

    assert "fake" in plugins


def test_load_plugins_untrusted_dist():
    ep = _make_entry_point("evil", "evil-package", "ddtrace.some.module")

    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations._get_integration_entry_points", return_value=[ep]):
        plugins = registry._load()

    assert "evil" not in plugins


def test_load_plugins_untrusted_module_path():
    """A package that claims dist Name='ddtrace' but ships code outside the ddtrace namespace."""
    ep = _make_entry_point("evil", "ddtrace", "evil_package.module")

    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations._get_integration_entry_points", return_value=[ep]):
        plugins = registry._load()

    assert "evil" not in plugins


def test_load_plugins_no_dist_attribute():
    """When EntryPoint.dist is absent (Python < 3.10), trust relies solely on the module path."""
    ep = _make_entry_point("fake", "ddtrace", "ddtrace.some.module")
    del ep.dist

    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations._get_integration_entry_points", return_value=[ep]):
        plugins = registry._load()

    assert "fake" in plugins


def test_registry_get_and_iter_cache_plugins():
    ep = _make_entry_point("fake", "ddtrace", "ddtrace.some.module")

    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations._get_integration_entry_points", return_value=[ep]) as get_eps:
        plugin = registry.get("fake")
        assert plugin is not None
        assert plugin.name == "fake"
        assert list(registry) == [plugin]
        # Cached after the first call: entry points are only ever fetched once.
        get_eps.assert_called_once()


def test_registry_get_unknown_plugin_returns_none():
    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations._get_integration_entry_points", return_value=[]):
        assert registry.get("does-not-exist") is None


# --- IntegrationRegistry.enable_plugin()/disable_plugin(): the only callers of a plugin's own
# enable()/disable(), and thus the only place that needs to (and does) enforce idempotency -- a
# plugin never tracks its own "am I patched" state, and that state lives on the registry instance,
# not module-level, so each test below uses its own fresh IntegrationRegistry() for isolation.
# Checks supported_versions (distribution name -> spec) against distribution metadata (never by
# importing anything) before calling a plugin's own enable() ---


class _FakeEnablePlugin:
    def __init__(self, name: str, supported_versions: Optional[dict[str, str]] = None) -> None:
        self.name = name
        self.supported_versions = supported_versions or {}
        self.enable_calls = 0
        self.disable_calls = 0

    def enable(self) -> None:
        self.enable_calls += 1

    def disable(self) -> None:
        self.disable_calls += 1


def test_enable_plugin_no_supported_versions_calls_enable_immediately():
    plugin = _FakeEnablePlugin("fake-o")
    with patch("ddtrace.internal.integrations.telemetry_writer") as telemetry_writer:
        IntegrationRegistry().enable_plugin(plugin)

    assert plugin.enable_calls == 1
    telemetry_writer.add_integration.assert_called_once_with("fake-o", True, True, "", version="")


def test_enable_plugin_compatible_calls_enable():
    plugin = _FakeEnablePlugin("fake-q", supported_versions={"fake-q-dist": ">=1.0"})
    with (
        patch("ddtrace.internal.integrations.telemetry_writer") as telemetry_writer,
        patch("ddtrace.internal.integrations.get_version_for_package", return_value="2.0.0") as get_version_for_package,
    ):
        IntegrationRegistry().enable_plugin(plugin)

    get_version_for_package.assert_called_once_with("fake-q-dist")
    assert plugin.enable_calls == 1
    telemetry_writer.add_integration.assert_called_once_with("fake-q", True, True, "", version="2.0.0")


def test_enable_plugin_incompatible_never_calls_enable():
    plugin = _FakeEnablePlugin("fake-r", supported_versions={"fake-r-dist": ">=99.0"})
    with (
        patch("ddtrace.internal.integrations.telemetry_writer") as telemetry_writer,
        patch("ddtrace.internal.integrations.get_version_for_package", return_value="1.0.0"),
    ):
        IntegrationRegistry().enable_plugin(plugin)

    assert plugin.enable_calls == 0
    telemetry_writer.add_integration.assert_called_once()
    args = telemetry_writer.add_integration.call_args.args
    assert args[0] == "fake-r"
    assert args[1] is False


def test_enable_plugin_no_installed_version_is_treated_as_compatible():
    """Matches check_module_compatibility's own "no installed version -> always patch" behavior --
    a distribution that isn't installed, or whose metadata couldn't be resolved, doesn't block
    enabling.
    """
    plugin = _FakeEnablePlugin("fake-n", supported_versions={"fake-n-dist": ">=99.0"})
    with (
        patch("ddtrace.internal.integrations.telemetry_writer") as telemetry_writer,
        patch("ddtrace.internal.integrations.get_version_for_package", return_value=""),
    ):
        IntegrationRegistry().enable_plugin(plugin)

    assert plugin.enable_calls == 1
    telemetry_writer.add_integration.assert_called_once_with("fake-n", True, True, "", version="")


def test_enable_plugin_twice_only_calls_enable_once():
    """The centralized idempotency guarantee: a plugin's own enable() never needs its own
    double-activation guard, because enable_plugin() itself only ever calls it once per on/off cycle.
    """
    plugin = _FakeEnablePlugin("fake-s")
    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations.telemetry_writer"):
        registry.enable_plugin(plugin)
        registry.enable_plugin(plugin)

    assert plugin.enable_calls == 1


def test_disable_plugin_calls_disable_and_allows_re_enable():
    plugin = _FakeEnablePlugin("fake-t")
    registry = IntegrationRegistry()
    with patch("ddtrace.internal.integrations.telemetry_writer"):
        registry.enable_plugin(plugin)
        registry.disable_plugin(plugin)

    assert plugin.enable_calls == 1
    assert plugin.disable_calls == 1

    with patch("ddtrace.internal.integrations.telemetry_writer"):
        registry.enable_plugin(plugin)

    assert plugin.enable_calls == 2  # re-enabling after a real disable() genuinely re-activates


def test_disable_plugin_not_enabled_is_a_noop():
    plugin = _FakeEnablePlugin("fake-u")
    IntegrationRegistry().disable_plugin(plugin)

    assert plugin.disable_calls == 0


def test_enable_plugin_without_a_config_does_not_report_configuration():
    plugin = _FakeEnablePlugin("fake-v")
    with (
        patch("ddtrace.internal.integrations.telemetry_writer"),
        patch("ddtrace.internal.integrations.report_configuration") as report_configuration,
    ):
        IntegrationRegistry().enable_plugin(plugin)

    report_configuration.assert_not_called()


def test_enable_plugin_reports_plugin_config_via_telemetry():
    """enable_plugin() reports the plugin's own IntegrationEnvConfig (if it has one) via
    report_configuration() -- the same call ProductManager already makes for a product's own
    config, just never wired up for integrations until now.
    """
    module_name = "tests.internal._fake_plugin_module_e"
    _make_fake_plugin_module(module_name, "fake_plugin_e")
    try:
        plugin_config_cls = type("_FakeConfig", (IntegrationEnvConfig,), {"__module__": module_name})
        plugin_config = config.fake_plugin_e
        assert isinstance(plugin_config, plugin_config_cls)

        plugin = _FakeEnablePlugin("fake_plugin_e")
        with (
            patch("ddtrace.internal.integrations.telemetry_writer"),
            patch("ddtrace.internal.integrations.report_configuration") as report_configuration,
        ):
            IntegrationRegistry().enable_plugin(plugin)

        report_configuration.assert_called_once_with(plugin_config)
        assert plugin.enable_calls == 1
    finally:
        _cleanup_fake_plugin(module_name, "fake_plugin_e")


# --- IntegrationEnvConfig.__init_subclass__: self-registration, no decorator needed ---


def _make_fake_plugin_module(
    module_name: str, integration_name: Optional[str], supported_versions: Optional[dict[str, str]] = None
) -> types.ModuleType:
    module = types.ModuleType(module_name)
    if integration_name is not None:
        module.name = integration_name  # type: ignore[attr-defined]
    if supported_versions is not None:
        module.supported_versions = supported_versions  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    return module


def _cleanup_fake_plugin(module_name: str, integration_name: str) -> None:
    del sys.modules[module_name]
    config._integration_configs.pop(integration_name, None)
    _pending_plugin_configs.pop(integration_name, None)


def test_integration_env_config_self_registers():
    """Subclassing IntegrationEnvConfig, given a module-level `name`, is the whole declaration --
    no decorator, no explicit `config = MyConfig()` line. See ddtrace/internal/settings/integration.py.
    """
    module_name = "tests.internal._fake_plugin_module_a"
    _make_fake_plugin_module(module_name, "fake_plugin_a")
    try:
        type("_FakeConfig", (IntegrationEnvConfig,), {"__module__": module_name})

        assert config.fake_plugin_a.integration_name == "fake_plugin_a"
        assert isinstance(config.fake_plugin_a, IntegrationEnvConfig)
    finally:
        _cleanup_fake_plugin(module_name, "fake_plugin_a")


def test_integration_env_config_requires_module_name():
    module_name = "tests.internal._fake_plugin_module_b"
    _make_fake_plugin_module(module_name, integration_name=None)  # no `name` on the module
    try:
        with pytest.raises(TypeError):
            type("_FakeConfig", (IntegrationEnvConfig,), {"__module__": module_name})
    finally:
        del sys.modules[module_name]


def test_integration_env_config_duplicate_registration_raises():
    module_name = "tests.internal._fake_plugin_module_c"
    _make_fake_plugin_module(module_name, "fake_plugin_c")
    try:
        type("_FakeConfig", (IntegrationEnvConfig,), {"__module__": module_name})

        with pytest.raises(TypeError):
            type("_FakeConfig2", (IntegrationEnvConfig,), {"__module__": module_name})
    finally:
        _cleanup_fake_plugin(module_name, "fake_plugin_c")


def test_integration_env_config_reorders_bases_so_mixin_init_runs():
    """IntegrationEnvConfig is moved to the end of cls.__bases__ regardless of declared order, so a
    mixin's own __init__ -- which cooperatively calls super().__init__() -- actually runs. Without
    this, declaring IntegrationEnvConfig first would mean envier's Env.__init__ (which never calls
    super().__init__() itself) stops the chain before HttpIntegrationConfigMixin.__init__ ever sets
    `self.http`.
    """
    from ddtrace._trace.settings import HttpIntegrationConfigMixin

    module_name = "tests.internal._fake_plugin_module_d"
    _make_fake_plugin_module(module_name, "fake_plugin_d")
    try:
        # Deliberately the "wrong" order: IntegrationEnvConfig declared before the mixin.
        cls = type("_FakeConfig", (IntegrationEnvConfig, HttpIntegrationConfigMixin), {"__module__": module_name})

        assert cls.__bases__[-1] is IntegrationEnvConfig
        assert hasattr(config.fake_plugin_d, "http")
    finally:
        _cleanup_fake_plugin(module_name, "fake_plugin_d")
