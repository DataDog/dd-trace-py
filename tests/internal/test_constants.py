import warnings

import pytest


def test_not_deprecated():
    import ddtrace

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.ENV_KEY
        assert len(ws) == 0


def test_invalid():
    with pytest.raises(ImportError):
        from ddtrace.constants import INVALID_CONSTANT  # noqa:F401


def test_sampling_priorities_are_public():
    """ddtrace.constants re-exports the sampling priorities that ddtrace.internal.constants defines."""
    from ddtrace import constants
    from ddtrace.internal import constants as internal_constants

    expected = {"USER_REJECT": -1, "AUTO_REJECT": 0, "AUTO_KEEP": 1, "USER_KEEP": 2}
    assert {name: getattr(constants, name) for name in expected} == expected
    assert all(getattr(constants, name) is getattr(internal_constants, name) for name in expected)


MOVED_CONSTANTS = {
    "ENV_KEY": "env",
    "VERSION_KEY": "version",
    "_USER_ID_KEY": "_dd.p.usr.id",
    "_CONFIG_ENDPOINT_ENV": "_DD_CONFIG_ENDPOINT",
    "_CONFIG_ENDPOINT_RETRIES_ENV": "_DD_CONFIG_ENDPOINT_RETRIES",
    "_CONFIG_ENDPOINT_TIMEOUT_ENV": "_DD_CONFIG_ENDPOINT_TIMEOUT",
}


def test_constants_needed_by_shared_code_stay_public():
    """ddtrace.constants keeps the names and values it had before they were defined in internal constants."""
    from ddtrace import constants
    from ddtrace.internal import constants as internal_constants

    assert {name: getattr(constants, name) for name in MOVED_CONSTANTS} == MOVED_CONSTANTS
    assert all(getattr(constants, name) is getattr(internal_constants, name) for name in MOVED_CONSTANTS)


@pytest.mark.parametrize(
    "module",
    [
        "ddtrace.internal.settings._otel_remapper",
        "ddtrace.internal.settings.endpoint_config",
        "ddtrace.internal.utils.http",
    ],
)
def test_shared_modules_do_not_import_public_constants(module):
    import ast
    import importlib
    from pathlib import Path

    tree = ast.parse(Path(importlib.import_module(module).__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "ddtrace.constants" not in imported


def test_internal_constants_do_not_import_public_constants():
    import ast
    from pathlib import Path

    from ddtrace.internal import constants as internal_constants

    tree = ast.parse(Path(internal_constants.__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not {name for name in imported if name and name.split(".")[0] == "ddtrace"}
