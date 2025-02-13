import os

from ddtrace.internal.native import _apply_configuration_from_disk
from ddtrace.internal.native import get_configuration_from_disk


def test_get_configuration_from_disk__priority(tmp_path):
    """
    Verify the order:
    local stable config < environment variables < managed stable config
    """

    local_config = tmp_path / "local_config.yaml"
    local_config.write_text(
        """
apm_configuration_default:
  DD_SERVICE: "a"
""",
        encoding="utf-8",
    )

    managed_config = tmp_path / "managed_config.yaml"
    managed_config.write_text(
        """
apm_configuration_default:
  DD_SERVICE: "c"
""",
        encoding="utf-8",
    )

    # First test, local config
    _apply_configuration_from_disk(local_file_override=str(local_config))
    assert os.environ["DD_SERVICE"] == "a"
    del os.environ["DD_SERVICE"]

    # Second test, local config + environment variable
    os.environ["DD_SERVICE"] = "b"
    _apply_configuration_from_disk(local_file_override=str(local_config))
    assert os.environ["DD_SERVICE"] == "b"
    del os.environ["DD_SERVICE"]

    # Third test, local config + environment variable + managed config
    _apply_configuration_from_disk(local_file_override=str(local_config), managed_file_override=str(managed_config))
    assert os.environ["DD_SERVICE"] == "c"
    del os.environ["DD_SERVICE"]


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

    config = get_configuration_from_disk(local_file_override=str(config_1))
    assert len(config) == 1
    assert config[0]["name"] == "DD_RUNTIME_METRICS_ENABLED"
    assert config[0]["value"] == "true"


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

    config = get_configuration_from_disk(local_file_override=str(config_1))
    assert len(config) == 1
    assert config[0]["name"] == "DD_SERVICE"
    assert config[0]["value"] == "my-service"

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

    config = get_configuration_from_disk(local_file_override=str(config_2))
    assert len(config) == 0


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
