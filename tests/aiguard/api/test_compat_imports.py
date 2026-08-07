"""Backwards-compatibility tests for the deprecated ``ddtrace.appsec.ai_guard`` path.

AI Guard moved to the top-level ``ddtrace.aiguard`` package. The old location is
kept as a lazy re-export shim until 5.0.0 and must keep working while emitting a
``DDTraceDeprecationWarning`` on access.
"""

import importlib
import sys
import warnings

import pytest

import ddtrace.aiguard as new_pkg
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning


_PUBLIC_SYMBOLS = [
    "new_ai_guard_client",
    "AIGuardClient",
    "AIGuardClientError",
    "AIGuardAbortError",
    "ContentPart",
    "Evaluation",
    "Function",
    "ImageURL",
    "Message",
    "Options",
    "ToolCall",
]

# The Strands classes are NOT re-exported from the top-level ``ddtrace.aiguard``
# package; they live in ``ddtrace.aiguard.integrations.strands`` (and may be
# stubs when strands isn't installed). The deprecated path forwards them there.
_STRANDS_SYMBOLS = [
    "AIGuardStrandsPlugin",
    "AIGuardStrandsHookProvider",
]


@pytest.mark.parametrize("name", _PUBLIC_SYMBOLS)
def test_old_path_reexports_new_symbol(name):
    old_pkg = importlib.import_module("ddtrace.appsec.ai_guard")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DDTraceDeprecationWarning)
        assert getattr(old_pkg, name) is getattr(new_pkg, name)


@pytest.mark.parametrize("name", _STRANDS_SYMBOLS)
def test_old_path_reexports_strands_symbol_from_submodule(name):
    old_pkg = importlib.import_module("ddtrace.appsec.ai_guard")
    strands_mod = importlib.import_module("ddtrace.aiguard.integrations.strands")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DDTraceDeprecationWarning)
        assert getattr(old_pkg, name) is getattr(strands_mod, name)


@pytest.mark.parametrize("name", _STRANDS_SYMBOLS)
def test_strands_symbols_not_on_top_level_package(name):
    assert not hasattr(new_pkg, name)


def test_old_path_access_warns():
    # Evict from sys.modules so a fresh import clears the shim's per-symbol
    # warning cache (the shim caches resolved names into globals()); __getattr__
    # only fires on a name that isn't already in the module dict.
    sys.modules.pop("ddtrace.appsec.ai_guard", None)
    old_pkg = importlib.import_module("ddtrace.appsec.ai_guard")
    with pytest.warns(DDTraceDeprecationWarning, match="ddtrace.appsec.ai_guard is deprecated"):
        old_pkg.AIGuardClient


def test_unknown_attribute_still_raises():
    old_pkg = importlib.import_module("ddtrace.appsec.ai_guard")
    with pytest.raises(AttributeError):
        old_pkg.DoesNotExist
