import importlib
import importlib.machinery
import sys
import types

import pytest

import ddtrace.internal.codeowners as codeowners_module
from ddtrace.internal.codeowners import Codeowners


def test_invalid_codeowners(testdir):
    """Skip invalid lines and still match valid rules."""
    codeowners = """
    [invalid section
    * @default

    ^[invalid optional section
    bar.py @bars
    # Inline comment case
    baz.py @DataDog/the-owner  # all that should be ignored
    """
    codeowners_file = testdir.makefile("", CODEOWNERS=codeowners)

    c = Codeowners(path=codeowners_file.strpath)
    assert c.of("foo.py") == ["@default"]
    assert c.of("bar.py") == ["@bars"]
    assert c.of("baz.py") == ["@DataDog/the-owner"]


def test_bridge_prefers_installed_provider():
    """The bridge re-exports the extracted provider when it is importable.

    ddtrace.internal.codeowners is a bridge to _ddtrace_internal_base.codeowners
    (package split, see docs/package-split/plan.md); an installed provider must
    win over the vendored fallback.
    """
    fake_pkg = types.ModuleType("_ddtrace_internal_base")
    fake_pkg.__path__ = []
    marker = object()
    fake_mod = types.ModuleType("_ddtrace_internal_base.codeowners")
    fake_mod.Codeowners = marker
    fake_mod.path_to_regex = object()
    try:
        sys.modules["_ddtrace_internal_base"] = fake_pkg
        sys.modules["_ddtrace_internal_base.codeowners"] = fake_mod
        reloaded = importlib.reload(codeowners_module)
        assert reloaded.Codeowners is marker
    finally:
        sys.modules.pop("_ddtrace_internal_base", None)
        sys.modules.pop("_ddtrace_internal_base.codeowners", None)
        importlib.reload(codeowners_module)


def test_bridge_surfaces_broken_provider():
    """A provider that is installed but cannot be imported is a broken installation.

    The bridge may fall back only for an absent provider; a broken one must
    surface loudly instead of silently switching to the vendored copy.
    """
    fake_pkg = types.ModuleType("_ddtrace_internal_base")
    fake_pkg.__path__ = []
    fake_pkg.__spec__ = importlib.machinery.ModuleSpec("_ddtrace_internal_base", None, is_package=True)
    try:
        sys.modules["_ddtrace_internal_base"] = fake_pkg
        with pytest.raises(ModuleNotFoundError, match="_ddtrace_internal_base.codeowners"):
            importlib.reload(codeowners_module)
    finally:
        sys.modules.pop("_ddtrace_internal_base", None)
        importlib.reload(codeowners_module)
