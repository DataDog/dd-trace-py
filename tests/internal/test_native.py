import json
from unittest.mock import Mock
from unittest.mock import call

import pytest

from ddtrace.internal import telemetry
from ddtrace.internal.native._native import PyCoreConfigResolver


def test_core_config_resolver_reads_manifest_and_applies_programmatic_values(tmp_path):
    manifest = tmp_path / "supported-configurations.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "2",
                "supportedConfigurations": {
                    "DD_SAMPLE_RATE": [{"type": "decimal", "default": "1", "configurationNames": ["sample_rate"]}]
                },
            }
        ),
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "initialize",
        "manifest": {"kind": "path", "value": str(manifest)},
        "inputs": {
            "layers": [
                {"origin": "env_var", "entries": [{"name": "DD_SAMPLE_RATE", "value": "0.5"}]},
                {
                    "origin": "code",
                    "entries": [{"name": "sample_rate", "value": {"kind": "number", "value": 0.25}}],
                },
            ]
        },
    }
    result = json.loads(PyCoreConfigResolver().request(json.dumps(request).encode()))
    assert result["status"] == "complete"
    assert result["settings"][0]["resolved"] == {
        "value": {"kind": "number", "value": 0.25},
        "origin": "code",
        "input_name": "sample_rate",
    }


@pytest.mark.parametrize(("name", "otel_env"), [("DD_ENV", None), ("DD_SERVICE", "OTEL_SERVICE_NAME")])
def test_core_config_preserves_startup_string_origins(monkeypatch, name, otel_env):
    resolver = PyCoreConfigResolver()
    resolve = Mock(wraps=resolver.request)
    native_resolver = Mock(request=resolve)
    report = Mock()
    monkeypatch.setattr(telemetry._native, "PyCoreConfigResolver", lambda: native_resolver)
    monkeypatch.setattr(telemetry.telemetry_writer, "add_configuration", report)
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {name: "local"})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {name: "fleet"})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG_IDS", {name: "fleet-id"})
    monkeypatch.setenv(name, "process")
    if otel_env:
        monkeypatch.delenv(otel_env, raising=False)

    assert telemetry.get_config(name, "fallback", otel_env=otel_env) == "fleet"
    resolve.assert_called_once()
    assert report.call_args_list == [
        call(name, "fallback", "default"),
        call(name, "local", "local_stable_config"),
        call(name, "process", "env_var"),
        call(name, "fleet", "fleet_stable_config", "fleet-id"),
    ]


def test_core_config_uses_the_otel_service_mapping_when_set(monkeypatch):
    resolver = Mock()
    monkeypatch.setattr(telemetry._native, "PyCoreConfigResolver", resolver)
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {})
    monkeypatch.delenv("DD_SERVICE", raising=False)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "otel-service")

    assert telemetry.get_config("DD_SERVICE", otel_env="OTEL_SERVICE_NAME", report_telemetry=False) == "otel-service"
    resolver.assert_not_called()


def test_is_panic_exception():
    from ddtrace.internal.native.exceptions import is_panic_exception

    class PanicException(BaseException):
        pass

    # pyo3_runtime is not normally importable, so PanicException is identified
    # by name rather than by isinstance/issubclass.
    PanicException.__module__ = "pyo3_runtime"

    assert is_panic_exception(PanicException("boom")) is True
    assert is_panic_exception(ValueError("boom")) is False
    assert is_panic_exception(BaseException("boom")) is False


@pytest.mark.subprocess(env={"DD_VERSION": "b"})
def test_get_configuration_from_disk_managed_stable_config_priority():
    """
    Verify the order:
    local stable config < environment variables < managed stable config
    """
    import os
    import tempfile

    # Create managed config
    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="managed_config") as managed_config:
        managed_config.write(
            b"""
config_id: "123"
apm_configuration_default:
  DD_VERSION: "c"
"""
        )
        managed_config.flush()

        # Create local config
        with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
            local_config.write(
                b"""
apm_configuration_default:
  DD_VERSION: "a"
  """
            )
            local_config.flush()
            # Ensure managed and local configs can be discovered via envars
            os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name
            os.environ["_DD_SC_MANAGED_FILE_OVERRIDE"] = managed_config.name
            # Import ddtrace to apply configuration
            from ddtrace import config

            # Ensure managed configuration takes precedence over local config and envars
            assert config.version == "c", f"Expected DD_VERSION to be 'c' but got {config.version}"


@pytest.mark.subprocess(parametrize={"DD_TRACE_DEBUG": ["TRUE", "1"]}, err=None)
def test_get_configuration_debug_logs():
    """
    Verify stable config debug log enablement
    """
    import os
    import sys
    import tempfile

    from tests.utils import call_program

    # Create managed config
    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="managed_config") as managed_config:
        managed_config.write(
            b"""
apm_configuration_default:
  DD_VERSION: "c"
"""
        )
        managed_config.flush()

        env = os.environ.copy()
        env["DD_TRACE_DEBUG"] = "true"
        env["_DD_SC_MANAGED_FILE_OVERRIDE"] = managed_config.name

        _, err, status, _ = call_program(sys.executable, "-c", "import ddtrace", env=env)
        assert status == 0, err
        assert b"Read the following static config: StableConfig" in err
        assert b'ConfigMap([("DD_VERSION", "c")]), tags: {}, rules: [] }' in err
        assert b"configurator: Configurator { debug_logs: true }" in err


@pytest.mark.subprocess(parametrize={"DD_VERSION": ["b", None]})
def test_get_configuration_from_disk_local_config_priority(tmp_path):
    """
    Verify the order:
    local stable config < environment variables
    """
    import os
    import tempfile

    # Create local config
    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
apm_configuration_default:
  DD_VERSION: "a"
"""
        )
        local_config.flush()
        # Ensure managed and local configs can be discovered via envars
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name
        # Import ddtrace to apply configuration
        from ddtrace import config

        # Ensure environment variables takes precedence over local config and envars
        if "DD_VERSION" in os.environ:
            assert config.version == "b", f"Expected DD_VERSION to be 'b' but got {config.version}"
        else:
            assert config.version == "a", f"Expected DD_VERSION to be 'a' but got {config.version}"


@pytest.mark.subprocess()
def test_get_configuration_from_disk__host_selector(tmp_path):
    """
    Verify local configurations can be read from a file
    """
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
apm_configuration_default:
  DD_RUNTIME_METRICS_ENABLED: true
"""
        )
        local_config.flush()
        # Provide the local config via an environment variable
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name
        # Ensure runtime metrics is enabled (default value is False)
        from ddtrace import config

        config._runtime_metrics_enabled = True


@pytest.mark.subprocess()
def test_get_configuration_from_disk__service_selector_match():
    # First test -- config matches & should be returned
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
rules:
  - selectors:
    - origin: language
      matches:
        - python
      operator: equals
    configuration:
      DD_VERSION: my-version
"""
        )
        local_config.flush()
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name

        from ddtrace import config

        assert config.version == "my-version", f"Expected DD_VERSION to be 'my-version' but got {config.version}"


@pytest.mark.subprocess()
def test_get_configuration_from_disk__service_selector_not_matched():
    # Second test -- config does not match & should not be returned
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
rules:
  - selectors:
    - origin: language
      matches:
        - nodejs
      operator: equals
    configuration:
      DD_VERSION: my-version
"""
        )
        local_config.flush()
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name

        from ddtrace import config

        assert config.version != "my-version", f"Expected DD_VERSION to be 'my-version' but got {config.version}"
