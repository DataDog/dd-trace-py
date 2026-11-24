"""Tests for the supported-configurations registry checker's source scan."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import textwrap
import types
from typing import Any
from typing import Optional

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "supported_configurations.py"
_SPEC: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
    "supported_configurations", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE: types.ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


# check_registry() scans the dd-trace-py checkout (it reads ddtrace/_monkey.py for the integration list). CI runs this
# suite against an installed ddtrace whose source tree is not checked out, so those tests need the sources.
requires_source_tree = pytest.mark.skipif(
    not (_MODULE.REPO_ROOT / "ddtrace" / "_monkey.py").is_file(),
    reason="needs the dd-trace-py source tree, which is absent when testing an installed ddtrace",
)


def _registry(*names: str) -> dict[str, Any]:
    return {"supportedConfigurations": {name: [{}] for name in names}}


@pytest.fixture
def isolated_package_roots(monkeypatch):
    """Make the scanner see no installed split packages unless a test adds some."""
    _MODULE._package_source_roots.cache_clear()
    monkeypatch.setattr(_MODULE, "_package_source_roots", lambda: ())
    monkeypatch.setattr(_MODULE, "_STRING_CONSTANTS_CACHE", {})


@requires_source_tree
def test_check_registry_reports_unregistered_var_in_extra_root(tmp_path, capsys, isolated_package_roots):
    (tmp_path / "mod.py").write_text('import os\nos.environ.get("DD_PACKAGE_ONLY_VAR")\n')

    assert _MODULE.check_registry(_registry("DD_OTHER"), scan_roots=[tmp_path]) == 1

    out = capsys.readouterr().out
    assert "DD_PACKAGE_ONLY_VAR" in out
    assert str(tmp_path) in out


@requires_source_tree
def test_check_registry_accepts_registered_var_in_extra_root(tmp_path, capsys, isolated_package_roots):
    (tmp_path / "mod.py").write_text('import os\nos.environ.get("DD_PACKAGE_ONLY_VAR")\n')

    # The dynamically derived integration vars are absent from this tiny registry, so only look for our var.
    _MODULE.check_registry(_registry("DD_PACKAGE_ONLY_VAR"), scan_roots=[tmp_path])

    assert "DD_PACKAGE_ONLY_VAR" not in capsys.readouterr().out.split("Unregistered vars:")[-1]


@requires_source_tree
def test_check_registry_default_roots_are_ddtrace_and_installed_packages(
    tmp_path, capsys, monkeypatch, isolated_package_roots
):
    monkeypatch.setattr(_MODULE, "_package_source_roots", lambda: (tmp_path,))
    (tmp_path / "mod.py").write_text('X = "DD_INSTALLED_PACKAGE_VAR"\n')

    assert _MODULE.check_registry(_registry()) == 1

    out = capsys.readouterr().out
    assert "DD_INSTALLED_PACKAGE_VAR" in out
    assert str(_MODULE.REPO_ROOT / "ddtrace") in out


@requires_source_tree
def test_check_registry_default_roots_without_packages_scan_only_ddtrace(capsys, isolated_package_roots):
    # Nothing installed: only ddtrace/ is scanned, exactly as before the packages existed.
    _MODULE.check_registry(_registry())

    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line == f"Scanning for env vars in: {_MODULE.REPO_ROOT / 'ddtrace'}"


def test_package_source_roots_finds_installed_package(tmp_path, monkeypatch):
    package = tmp_path / "_ddtrace_internal"
    package.mkdir()
    (package / "__init__.py").write_text("")
    monkeypatch.delitem(sys.modules, "_ddtrace_internal", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    _MODULE._package_source_roots.cache_clear()
    try:
        assert _MODULE._package_source_roots() == (package,)
    finally:
        _MODULE._package_source_roots.cache_clear()
        sys.modules.pop("_ddtrace_internal", None)


def test_package_source_roots_skips_missing_packages(monkeypatch):
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "_ddtrace" not in p])
    monkeypatch.setattr(_MODULE, "SPLIT_PACKAGES", ("_ddtrace_does_not_exist",))
    _MODULE._package_source_roots.cache_clear()
    try:
        assert _MODULE._package_source_roots() == ()
    finally:
        _MODULE._package_source_roots.cache_clear()


def test_envier_constants_imported_from_installed_package_are_resolved(tmp_path, monkeypatch, isolated_package_roots):
    package = tmp_path / "_ddtrace_internal"
    package.mkdir()
    (package / "names.py").write_text('SETTING = "log_rate"\n')
    monkeypatch.setattr(_MODULE, "_package_source_roots", lambda: (package,))

    module = _MODULE.ast.parse(
        textwrap.dedent(
            """
            from _ddtrace_internal.names import SETTING

            class Config(DDConfig):
                __prefix__ = "dd_trace"
                rate = DDConfig.v(int, SETTING, default=1)
            """
        )
    )
    found: set[str] = set()
    _MODULE._scan_envier_module(module, found, {})

    assert found == {"DD_TRACE_LOG_RATE"}
