import pytest


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
        assert b'ConfigMap([(DdVersion, "c")]), tags: {}, rules: [] }' in err
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
