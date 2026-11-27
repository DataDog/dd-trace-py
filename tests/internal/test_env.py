import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
import types

import pytest

# Acquire the env submodule via sys.modules because
# ddtrace.internal.settings.__init__ re-exports dd_environ as `env`, which
# shadows the submodule name for normal `import ... as ...` forms.
from ddtrace.internal.settings.env import dd_environ  # noqa: I001


env_module = sys.modules["ddtrace.internal.settings.env"]
ENV_LOGGER = env_module.__name__


def _visible_environ() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not (key.startswith("OTEL_") and value == "")}


@pytest.fixture(autouse=True)
def reset_warned_keys():
    env_module.is_sensitive("")  # the registry is resolved lazily; the tests below read its globals directly
    env_module._warned_keys.clear()
    yield
    env_module._warned_keys.clear()


def test_registered_key_no_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DD_AGENT_HOST")
    assert caplog.text == ""


def test_unregistered_dd_key_logs_once(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DD_TOTALLY_BOGUS_XYZ")
        env_module._validate_key("DD_TOTALLY_BOGUS_XYZ")
    assert caplog.text.count("DD_TOTALLY_BOGUS_XYZ") == 1
    assert "Unsupported Datadog configuration variable accessed" in caplog.text


def test_unregistered_otel_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("OTEL_TOTALLY_BOGUS_XYZ")
    assert "OTEL_TOTALLY_BOGUS_XYZ" in caplog.text


def test_non_datadog_key_skipped(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("PATH")
        env_module._validate_key("HOME")
    assert caplog.text == ""


def test_unregistered_underscore_dd_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("_DD_TOTALLY_BOGUS_XYZ")
    assert "_DD_TOTALLY_BOGUS_XYZ" in caplog.text


def test_unregistered_datadog_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DATADOG_TOTALLY_BOGUS_XYZ")
    assert "DATADOG_TOTALLY_BOGUS_XYZ" in caplog.text


def test_alias_is_accepted(caplog):
    # Pick an arbitrary registered alias from the generated registry
    alias = next(iter(env_module._ALIAS_TARGETS))
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key(alias)
    assert caplog.text == ""


def test_deprecated_key_logs_deprecated_message(caplog):
    if not env_module._DEPRECATED_CONFIGURATIONS:
        pytest.skip("no deprecated configurations registered")
    deprecated_key = next(iter(env_module._DEPRECATED_CONFIGURATIONS))
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key(deprecated_key)
    assert "Deprecated Datadog configuration variable accessed" in caplog.text
    assert deprecated_key in caplog.text


def test_getitem_reads_from_os_environ(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "myhost.example.com")
    assert dd_environ["DD_AGENT_HOST"] == "myhost.example.com"


def test_getitem_raises_keyerror_when_missing(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    with pytest.raises(KeyError):
        dd_environ["DD_AGENT_HOST"]


def test_setitem_writes_to_os_environ(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    dd_environ["DD_AGENT_HOST"] = "set-via-dd-environ"
    try:
        assert os.environ["DD_AGENT_HOST"] == "set-via-dd-environ"
    finally:
        del os.environ["DD_AGENT_HOST"]


def test_delitem_removes_from_os_environ(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "x")
    del dd_environ["DD_AGENT_HOST"]
    assert "DD_AGENT_HOST" not in os.environ


def test_contains_true_for_set_key(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "x")
    assert "DD_AGENT_HOST" in dd_environ


def test_contains_false_for_unset_key(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    assert "DD_AGENT_HOST" not in dd_environ


def test_empty_otel_value_is_treated_as_unset(monkeypatch):
    key = "OTEL_SERVICE_NAME"
    monkeypatch.setenv(key, "")

    assert os.environ[key] == ""
    assert key not in dd_environ
    assert dd_environ.get(key) is None
    assert dd_environ.get(key, "default") == "default"
    with pytest.raises(KeyError):
        dd_environ[key]

    visible_environ = _visible_environ()
    assert len(dd_environ) == len(visible_environ)
    assert set(dd_environ) == set(visible_environ)
    assert dd_environ.copy() == visible_environ


@pytest.mark.parametrize(
    "key,value",
    [
        ("DD_SERVICE", ""),
        ("OTEL_SERVICE_NAME", " "),
    ],
)
def test_nonempty_otel_and_empty_non_otel_values_remain_set(monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    assert key in dd_environ
    assert dd_environ[key] == value
    assert key in dd_environ.copy()


def test_contains_warns_on_unregistered_key(caplog, monkeypatch):
    monkeypatch.delenv("DD_UNREGISTERED_CONTAINS_TEST", raising=False)
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        result = "DD_UNREGISTERED_CONTAINS_TEST" in dd_environ
    assert result is False
    assert "DD_UNREGISTERED_CONTAINS_TEST" in caplog.text


def test_len_and_iter_match_visible_environ():
    visible_environ = _visible_environ()
    assert len(dd_environ) == len(visible_environ)
    assert set(dd_environ) == set(visible_environ)


def test_copy_returns_plain_dict():
    snapshot = dd_environ.copy()
    assert isinstance(snapshot, dict)
    assert snapshot == _visible_environ()


def _pick_alias_pair() -> tuple[str, str]:
    """Return (canonical, legacy) for the first registered rename alias."""
    canonical, aliases = next(iter(env_module._CONFIGURATION_ALIASES.items()))
    return canonical, aliases[0]


def test_get_aliases_returns_the_registered_aliases():
    canonical, aliases = next(iter(env_module._CONFIGURATION_ALIASES.items()))
    assert env_module.get_aliases(canonical) == aliases
    assert env_module.get_aliases("DD_NOT_A_CONFIGURATION") == ()


def test_is_sensitive_follows_the_registered_sensitive_set():
    from ddtrace.internal.settings._supported_configurations import SENSITIVE_CONFIGURATIONS

    assert SENSITIVE_CONFIGURATIONS, "the registry should list sensitive configurations"
    assert all(env_module.is_sensitive(name) for name in SENSITIVE_CONFIGURATIONS)
    assert not env_module.is_sensitive("DD_SERVICE")


def test_getitem_falls_back_to_alias_when_canonical_missing(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.setenv(legacy, "from-legacy")
    assert dd_environ[canonical] == "from-legacy"


def test_getitem_prefers_canonical_over_alias(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.setenv(canonical, "from-canonical")
    monkeypatch.setenv(legacy, "from-legacy")
    assert dd_environ[canonical] == "from-canonical"


def test_getitem_raises_when_neither_canonical_nor_alias_set(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    with pytest.raises(KeyError):
        dd_environ[canonical]


def test_legacy_direct_access_does_not_fall_back_to_canonical(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.setenv(canonical, "from-canonical")
    monkeypatch.delenv(legacy, raising=False)
    with pytest.raises(KeyError):
        dd_environ[legacy]


def test_contains_true_when_only_alias_is_set(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.setenv(legacy, "x")
    assert canonical in dd_environ


def test_contains_false_when_neither_canonical_nor_alias_set(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    assert canonical not in dd_environ


def test_get_config_falls_back_to_alias_in_local_config(monkeypatch):
    from ddtrace.internal import telemetry as telemetry_mod

    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setattr(telemetry_mod, "LOCAL_CONFIG", {legacy: "from-local"})
    monkeypatch.setattr(telemetry_mod, "FLEET_CONFIG", {})

    val = telemetry_mod.get_config(canonical, default=None, report_telemetry=False)
    assert val == "from-local"


def test_get_config_falls_back_to_alias_in_fleet_config(monkeypatch):
    from ddtrace.internal import telemetry as telemetry_mod

    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setattr(telemetry_mod, "LOCAL_CONFIG", {})
    monkeypatch.setattr(telemetry_mod, "FLEET_CONFIG", {legacy: "from-fleet"})
    monkeypatch.setattr(telemetry_mod, "FLEET_CONFIG_IDS", {})

    val = telemetry_mod.get_config(canonical, default=None, report_telemetry=False)
    assert val == "from-fleet"


def _run_python(code: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_import_ddtrace_resolves_the_registry_from_the_entry_point():
    """ddtrace registers nothing at import; env resolves the entry point when a lookup needs it."""
    canonical, legacy = _pick_alias_pair()
    out = _run_python(
        f"""
        import ddtrace
        from ddtrace.internal.settings.env import dd_environ, register_supported_configurations

        assert dd_environ[{canonical!r}] == "from-alias"
        assert not register_supported_configurations([], {{}}, [])  # the entry point registry won
        assert dd_environ[{canonical!r}] == "from-alias"
        print("ok")
        """,
        {legacy: "from-alias"},
    )
    assert out == "ok"


_STANDALONE_ENV = textwrap.indent(
    textwrap.dedent(
        f"""
import importlib.util, sys

REGISTRY = "ddtrace.internal.settings._supported_configurations"
spec = importlib.util.spec_from_file_location("standalone_env", {env_module.__file__!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert "ddtrace" not in sys.modules and REGISTRY not in sys.modules
assert not mod._resolved and mod._SUPPORTED_CONFIGURATIONS is None
"""
    ),
    "        ",
)


def test_registry_is_loaded_lazily_on_first_lookup():
    out = _run_python(
        _STANDALONE_ENV
        + """
        mod.dd_environ.get("DD_TOTALLY_BOGUS_XYZ")  # validation needs the registry
        assert REGISTRY in sys.modules
        assert mod._resolved and mod._SUPPORTED_CONFIGURATIONS is not None
        assert mod.get_aliases("DD_SERVICE") is not None
        assert mod.is_sensitive("DD_API_KEY")
        print("ok")
        """,
        {},
    )
    assert out == "ok"


@pytest.mark.parametrize("lookup", ["get_aliases('DD_SERVICE')", "is_sensitive('DD_API_KEY')"])
def test_alias_and_sensitivity_lookups_resolve_the_registry(lookup):
    out = _run_python(
        _STANDALONE_ENV
        + f"""
        mod.{lookup}
        assert REGISTRY in sys.modules
        print("ok")
        """,
        {},
    )
    assert out == "ok"


def test_explicit_registration_wins_over_the_entry_point():
    out = _run_python(
        _STANDALONE_ENV
        + """
        assert mod.register_supported_configurations(["DD_API_KEY"], {"DD_SERVICE": ["DD_OLD"]}, [])
        assert mod.get_aliases("DD_SERVICE") == ["DD_OLD"]
        assert mod.get_aliases("DD_ENV") == ()
        assert not mod.is_sensitive("DD_API_KEY")
        assert REGISTRY not in sys.modules  # the entry point was never consulted
        print("ok")
        """,
        {},
    )
    assert out == "ok"


def test_failing_entry_point_behaves_as_unregistered(monkeypatch, caplog):
    monkeypatch.setattr(env_module, "_resolved", False)
    monkeypatch.setattr(env_module, "_SUPPORTED_CONFIGURATIONS", None)
    monkeypatch.setattr(env_module, "_CONFIGURATION_ALIASES", {})
    monkeypatch.setattr(env_module, "_SENSITIVE_CONFIGURATIONS", frozenset())

    def boom(group):
        raise ImportError("broken registry")

    monkeypatch.setattr(env_module, "_load_entry_point_module", boom)
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        assert env_module.dd_environ.get("DD_TOTALLY_BOGUS_XYZ") is None
        assert env_module.get_aliases("DD_SERVICE") == ()
        assert not env_module.is_sensitive("DD_API_KEY")
    assert env_module._resolved
    assert env_module._SUPPORTED_CONFIGURATIONS is None
    assert len([r for r in caplog.records if "Could not load" in r.getMessage()]) == 1
    assert "Unsupported Datadog configuration" not in caplog.text


def test_missing_entry_point_behaves_as_unregistered(monkeypatch):
    monkeypatch.setattr(env_module, "_resolved", False)
    monkeypatch.setattr(env_module, "_SUPPORTED_CONFIGURATIONS", None)
    monkeypatch.setattr(env_module, "_CONFIGURATION_ALIASES", {})
    monkeypatch.setattr(env_module, "_load_entry_point_module", lambda group: None)
    assert env_module.get_aliases("DD_SERVICE") == ()
    assert env_module._SUPPORTED_CONFIGURATIONS is None


def test_env_before_registration_is_exact_lookup_without_validation():
    """Loading env.py on its own (no ddtrace package, no registry) must be safe and inert."""
    canonical, legacy = _pick_alias_pair()
    out = _run_python(
        f"""
        import importlib.util, logging, sys

        logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, format="LOG %(message)s")
        spec = importlib.util.spec_from_file_location("standalone_env", {env_module.__file__!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._load_entry_point_module = lambda group: None  # no entry point: the registry stays unregistered
        assert "ddtrace" not in sys.modules

        env = mod.dd_environ
        assert env.get("DD_TOTALLY_BOGUS_XYZ") is None  # unregistered-key debug log is skipped
        assert {canonical!r} not in env  # aliases are not applied
        assert env[{legacy!r}] == "from-alias"  # exact-key lookup still works

        assert mod.register_supported_configurations(
            [{canonical!r}], {{{canonical!r}: [{legacy!r}]}}, []
        )
        assert env[{canonical!r}] == "from-alias"
        env.get("DD_TOTALLY_BOGUS_XYZ")
        print("done")
        """,
        {legacy: "from-alias"},
    )
    lines = out.splitlines()
    assert lines[-1] == "done"
    assert [line for line in lines if line.startswith("LOG ")] == [
        "LOG Unsupported Datadog configuration variable accessed: DD_TOTALLY_BOGUS_XYZ"
    ]


def test_aliases_and_sensitivity_are_empty_before_registration():
    """Without a registry, nothing has aliases and nothing is known to be sensitive."""
    out = _run_python(
        f"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("standalone_env", {env_module.__file__!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._load_entry_point_module = lambda group: None  # no entry point: the registry stays unregistered

        assert mod.get_aliases("DD_SERVICE") == ()
        assert not mod.is_sensitive("DD_API_KEY")

        assert mod.register_supported_configurations(["DD_API_KEY"], {{"DD_SERVICE": ["DD_OLD"]}}, [], ["DD_API_KEY"])
        assert mod.get_aliases("DD_SERVICE") == ["DD_OLD"]
        assert mod.is_sensitive("DD_API_KEY")
        print("done")
        """,
        {},
    )
    assert out == "done"


class _FakeDist:
    def __init__(self, name):
        self.metadata = {"Name": name}


class _FakeEntryPoint:
    group = ""

    def __init__(self, name, value, module=None):
        self.name = name
        self.value = value
        self.module = module
        self.loaded = 0

    def load(self):
        self.loaded += 1
        return self.module


def _registry_module(canonical, legacy):
    return types.SimpleNamespace(
        SUPPORTED_CONFIGURATIONS=frozenset({canonical, "DD_API_KEY"}),
        CONFIGURATION_ALIASES={canonical: [legacy]},
        DEPRECATED_CONFIGURATIONS=frozenset(),
        SENSITIVE_CONFIGURATIONS=frozenset({"DD_API_KEY"}),
    )


@pytest.fixture
def unregistered_env(monkeypatch):
    """Reset the module state as if no registry had been resolved yet; restore it afterwards."""
    monkeypatch.setattr(env_module, "_resolved", False)
    monkeypatch.setattr(env_module, "_resolving_thread", None)
    monkeypatch.setattr(env_module, "_SUPPORTED_CONFIGURATIONS", None)
    monkeypatch.setattr(env_module, "_DEPRECATED_CONFIGURATIONS", frozenset())
    monkeypatch.setattr(env_module, "_CONFIGURATION_ALIASES", {})
    monkeypatch.setattr(env_module, "_ALIAS_TARGETS", frozenset())
    monkeypatch.setattr(env_module, "_SENSITIVE_CONFIGURATIONS", frozenset())
    yield


def _declare_providers(monkeypatch, providers):
    """Make env resolve the registry from the given (entry point, distribution) pairs."""
    from ddtrace.internal.utils import entry_points as entry_points_utils

    monkeypatch.setattr(entry_points_utils, "entry_points_with_distributions", lambda group: providers)


def test_untrusted_provider_is_skipped_and_the_next_one_loads(monkeypatch, unregistered_env, caplog):
    # Fixed names: the real registry's aliases are not available (the fixture cleared them).
    canonical, legacy = "DD_SERVICE_FAKE", "DD_SERVICE_FAKE_LEGACY"

    class _Exploding(_FakeEntryPoint):
        def load(self):
            raise AssertionError("an untrusted provider must not be loaded")

    untrusted = _Exploding("evil", "evil_module._supported_configurations")
    trusted = _FakeEntryPoint(
        "ddtrace",
        "ddtrace.internal.settings._supported_configurations",
        module=_registry_module(canonical, legacy),
    )
    _declare_providers(monkeypatch, [(untrusted, _FakeDist("evil")), (trusted, _FakeDist("ddtrace"))])

    with caplog.at_level(logging.WARNING, logger=ENV_LOGGER):
        assert env_module.get_aliases(canonical) == [legacy]

    assert trusted.loaded == 1
    assert any("untrusted distribution 'evil'" in record.getMessage() for record in caplog.records)


def test_only_untrusted_providers_means_unregistered(monkeypatch, unregistered_env, caplog):
    untrusted = _FakeEntryPoint("evil", "evil_module._supported_configurations")
    _declare_providers(monkeypatch, [(untrusted, _FakeDist("evil"))])

    with caplog.at_level(logging.WARNING, logger=ENV_LOGGER):
        assert env_module.get_aliases("DD_SERVICE") == ()
        assert not env_module.is_sensitive("DD_API_KEY")

    assert env_module._resolved
    assert env_module._SUPPORTED_CONFIGURATIONS is None
    assert any("untrusted distribution 'evil'" in record.getMessage() for record in caplog.records)


def test_concurrent_first_lookup_waits_for_the_installed_registry(monkeypatch, unregistered_env):
    """A lookup that starts while another thread is resolving must see the full registry, not defaults."""
    canonical, legacy = "DD_SERVICE_FAKE", "DD_SERVICE_FAKE_LEGACY"
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.setenv(legacy, "from-legacy")

    resolving = threading.Event()
    release = threading.Event()

    def slow_load(group):
        resolving.set()
        assert release.wait(timeout=10), "the test never released the loader"
        return _registry_module(canonical, legacy)

    monkeypatch.setattr(env_module, "_load_entry_point_module", slow_load)

    resolver = threading.Thread(target=env_module._resolve)
    resolver.start()
    assert resolving.wait(timeout=10), "the resolver never started loading"

    results = {}

    def concurrent_lookup():
        try:
            results["value"] = env_module.dd_environ[canonical]
        except Exception as exc:  # noqa: BLE001 -- recorded and asserted below
            results["error"] = exc

    reader = threading.Thread(target=concurrent_lookup)
    reader.start()
    time.sleep(0.05)  # let the reader reach the resolution lock while the registry is still loading
    release.set()
    reader.join(timeout=10)
    resolver.join(timeout=10)

    assert "error" not in results
    assert results["value"] == "from-legacy"  # the concurrent lookup saw the installed aliases


def test_reentrant_lookup_during_the_load_does_not_deadlock(monkeypatch, unregistered_env):
    """An env lookup made by the registry module while it loads proceeds unregistered instead of recursing."""
    canonical, legacy = "DD_SERVICE_FAKE", "DD_SERVICE_FAKE_LEGACY"
    observed = {}

    def load(group):
        observed["resolving_thread"] = env_module._resolving_thread
        observed["resolved_during_load"] = env_module._resolved
        # The registry module reads a configuration while it imports: re-entry, same thread.
        observed["nested_value"] = env_module.dd_environ.get("DD_NOT_IN_THIS_FAKE_REGISTRY")
        return _registry_module(canonical, legacy)

    monkeypatch.setattr(env_module, "_load_entry_point_module", load)

    assert env_module.is_sensitive("DD_API_KEY")  # triggers the load, which re-enters above

    assert observed["resolving_thread"] == threading.get_ident()
    assert observed["resolved_during_load"] is False
    assert observed["nested_value"] is None
    assert env_module._resolved and env_module._SUPPORTED_CONFIGURATIONS is not None
    assert env_module.get_aliases(canonical) == [legacy]
