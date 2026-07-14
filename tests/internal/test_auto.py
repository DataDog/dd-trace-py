import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12, 5), reason="Python < 3.12.5 eagerly loads the threading module")
@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
        DD_REMOTE_CONFIGURATION_ENABLED="1",
    )
)
def test_auto():
    import sys

    import ddtrace.auto  # noqa:F401

    assert "threading" not in sys.modules


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_yaml_not_unloaded():
    # Regression test for APMS-20000. Module cleanup used to drop and reimport
    # yaml/_yaml, leaving consumers with stale cached loader references.
    import sys

    import pytest

    import ddtrace

    yaml = pytest.importorskip("yaml")
    assert "yaml" not in ddtrace.LOADED_MODULES

    orig_load = yaml.load
    SafeLoader = yaml.SafeLoader
    CSafeLoader = getattr(yaml, "CSafeLoader", None)
    had_cyaml = "_yaml" in sys.modules

    import ddtrace.auto  # noqa:F401

    assert "yaml" in sys.modules
    if had_cyaml:
        assert "_yaml" in sys.modules

    import yaml as yaml_reimported

    assert yaml_reimported is yaml
    assert orig_load("a: 1\nb: 2\n", SafeLoader) == {"a": 1, "b": 2}
    if CSafeLoader is not None:
        assert orig_load("a: 1\nb: 2\n", CSafeLoader) == {"a": 1, "b": 2}
