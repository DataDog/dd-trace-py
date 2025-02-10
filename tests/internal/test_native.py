from ddtrace.internal.native import get_configuration_from_disk


def test_get_configuration_from_disk__host_selector(tmp_path):
    # First test -- config matches & should be returned
    config_1 = tmp_path / "config_1.yaml"
    config_1.write_text(
        """
apm_configuration_default:
  DD_RUNTIME_METRICS_ENABLED: true
""",
        encoding="utf-8",
    )

    config = get_configuration_from_disk(file_override=str(config_1))
    assert config == {"DD_RUNTIME_METRICS_ENABLED": "true"}


def test_get_configuration_from_disk__service_selector(tmp_path):
    # First test -- config matches & should be returned
    config_1 = tmp_path / "config_1.yaml"
    config_1.write_text(
        """
rules:
  - selectors:
    - origin: language
      matches:
        - python
      operator: equals
    configuration:
      DD_SERVICE: my-service
""",
        encoding="utf-8",
    )

    config = get_configuration_from_disk(file_override=str(config_1))
    assert config == {"DD_SERVICE": "my-service"}

    # Second test -- config does not match & should not be returned
    config_2 = tmp_path / "config_2.yaml"
    config_2.write_text(
        """
rules:
  - selectors:
    - origin: language
      matches:
        - nodejs
      operator: equals
    configuration:
      DD_SERVICE: my-service
""",
        encoding="utf-8",
    )

    config = get_configuration_from_disk(file_override=str(config_2))
    assert config == {}


def is_imported_before(module_a, module_b):
    import sys

    # Check if both modules are in sys.modules
    if module_a in sys.modules and module_b in sys.modules:
        # Get the position of the modules in sys.modules (which is an OrderedDict in Python 3.7+)
        modules = list(sys.modules.keys())
        return modules.index(module_a) < modules.index(module_b)
    return False  # If one or both modules are not imported, return False


def test_native_before_settings():
    # Ensure that the native module is imported before the settings module
    import ddtrace  # noqa: F401

    assert is_imported_before("ddtrace.internal.native", "ddtrace.settings")
