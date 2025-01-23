from ddtrace.internal.native import get_configuration_from_disk


def test_get_configuration_from_disk(tmp_path):
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
