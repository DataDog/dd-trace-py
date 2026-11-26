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


def test_tag_keys_needed_by_shared_settings_stay_public():
    """ddtrace.constants keeps the tag keys and values it had before they were defined in internal constants."""
    from ddtrace import constants
    from ddtrace.internal import constants as internal_constants

    expected = {"ENV_KEY": "env", "VERSION_KEY": "version"}
    assert {name: getattr(constants, name) for name in expected} == expected
    assert all(getattr(constants, name) is getattr(internal_constants, name) for name in expected)


def test_otel_remapper_does_not_import_public_constants():
    import ast
    from pathlib import Path

    from ddtrace.internal.settings import _otel_remapper

    tree = ast.parse(Path(_otel_remapper.__file__).read_text())
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
