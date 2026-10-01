"""Installed native WAF and tracer configuration agree on the linked library."""

import ast
from pathlib import Path

import pytest

from ddtrace.internal.native import _native
from ddtrace.internal.settings.asm import config


ddwaf = getattr(_native, "ddwaf", None)
pytestmark = pytest.mark.skipif(ddwaf is None, reason="native WAF feature unavailable")


def test_the_installed_library_version_matches_the_build_pin():
    tree = ast.parse((Path(__file__).resolve().parents[2] / "setup.py").read_text())
    pinned = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "LIBDDWAF_VERSION" for target in node.targets)
    )
    assert config._asm_libddwaf_available
    assert config._ddwaf_version == pinned
    assert ddwaf.version() == pinned
