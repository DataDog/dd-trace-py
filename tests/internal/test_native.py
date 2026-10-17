import pytest


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


@pytest.mark.subprocess(
    env={
        "_DD_SC_LOCAL_FILE_OVERRIDE": "/does/not/exist/local.yaml",
        "_DD_SC_MANAGED_FILE_OVERRIDE": "/does/not/exist/fleet.yaml",
    }
)
def test_stable_config_not_loaded_when_no_files_present():
    """No stable-configuration file exists.

    The fast path in ``ddtrace.internal.native`` must produce the same (empty)
    configuration the native reader would, without reading any file.
    """
    from ddtrace.internal.settings import _core

    assert _core.FLEET_CONFIG == {}
    assert _core.LOCAL_CONFIG == {}
    assert _core.FLEET_CONFIG_IDS == {}


@pytest.mark.subprocess(
    env={
        "_DD_SC_LOCAL_FILE_OVERRIDE": "/does/not/exist/local.yaml",
        "_DD_SC_MANAGED_FILE_OVERRIDE": "/does/not/exist/fleet.yaml",
    }
)
def test_settings_import_does_not_import_native_extension():
    """Importing the settings machinery must not load the compiled extension.

    When no stable-configuration file is present, the reader's fast path returns
    empty configuration without constructing a PyConfigurator, so the settings
    chain must never import the extension on its own accord.

    Note that other ddtrace import paths (e.g. ``ddtrace.internal.utils.formats``)
    load the extension for their own purposes; what this test forbids is loading
    it *from* the settings machinery.
    """
    import inspect
    import sys

    native_import_stacks = []

    def record_native_import(event, args):
        # Record the stack of every import of the compiled extension so the
        # test can attribute the load to its importer.
        if event == "import" and args[0] == "ddtrace.internal.native._native":
            filenames = []
            frame = inspect.currentframe().f_back
            while frame is not None:
                filenames.append(frame.f_code.co_filename)
                frame = frame.f_back
            native_import_stacks.append(filenames)

    sys.addaudithook(record_native_import)

    import ddtrace.internal.settings._core as _core

    assert _core.FLEET_CONFIG == {}
    assert _core.LOCAL_CONFIG == {}
    assert _core.FLEET_CONFIG_IDS == {}

    # Sanity-check the audit hook against reality before relying on it.
    if "ddtrace.internal.native._native" in sys.modules:
        assert native_import_stacks, "failed to record the native extension load"

    offenders = [
        filename
        for stack in native_import_stacks
        for filename in stack
        if filename.replace("\\", "/").endswith("internal/settings/_core.py")
    ]
    assert not offenders, "the settings machinery must not load the native extension: %s" % offenders


def test_get_configuration_from_disk_no_files(monkeypatch, tmp_path):
    """With no stable-configuration file present, the reader returns empty
    configuration without constructing the PyConfigurator.
    """
    import ddtrace.internal.native as native_module
    import ddtrace.internal.native._native as _native
    from ddtrace.internal.native._native import PyConfigurator

    monkeypatch.setattr(native_module, "_FLEET_STABLE_CONFIGURATION_PATH", str(tmp_path / "fleet.yaml"))
    monkeypatch.setattr(native_module, "_LOCAL_STABLE_CONFIGURATION_PATH", str(tmp_path / "local.yaml"))

    def _unexpected_configurator(*args, **kwargs):
        raise AssertionError("PyConfigurator must not be constructed when no stable-configuration file exists")

    monkeypatch.setattr(_native, "PyConfigurator", _unexpected_configurator)

    assert native_module.get_configuration_from_disk() == ({}, {}, {})

    # Test-only file overrides take precedence over the default paths.
    monkeypatch.setenv("_DD_SC_LOCAL_FILE_OVERRIDE", str(tmp_path / "override.yaml"))
    assert native_module.get_configuration_from_disk() == ({}, {}, {})

    # With an existing override file the reader is invoked.
    monkeypatch.setattr(_native, "PyConfigurator", PyConfigurator)
    (tmp_path / "override.yaml").write_text('apm_configuration_default:\n  DD_VERSION: "a"\n')
    fleet_config, local_config, fleet_config_ids = native_module.get_configuration_from_disk()
    assert fleet_config == {}
    assert fleet_config_ids == {}
    assert local_config == {"DD_VERSION": "a"}


@pytest.mark.subprocess
def test_stable_config_paths_match_native():
    """The paths mirrored in ``ddtrace.internal.native`` must stay in sync with libdatadog."""
    import ddtrace.internal.native as native_module
    from ddtrace.internal.native import stable_configuration_paths

    fleet, local = stable_configuration_paths()
    assert fleet == native_module._FLEET_STABLE_CONFIGURATION_PATH
    assert local == native_module._LOCAL_STABLE_CONFIGURATION_PATH
