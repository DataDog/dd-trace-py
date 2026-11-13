"""Unit tests for the Rust toolchain version check used when building from source."""

import ast
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def check_toolchain(monkeypatch):
    # Load the real check without running setup.py's native build configuration.
    setup_path = Path(__file__).resolve().parents[2] / "setup.py"
    tree = ast.parse(setup_path.read_text())
    tree.body = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "check_rust_toolchain")
        or (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "RUST_MINIMUM_VERSION"
        )
    ]
    namespace = {"subprocess": subprocess}
    exec(compile(tree, str(setup_path), "exec"), namespace)  # noqa: S102
    versions = {"rustc": "1.100.0", "cargo": "1.100.0"}

    def run(command, **kwargs):
        output = f"{command[0]} {versions[command[0]]} (0ef1bc386 2026-11-09)\n".encode()
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    return namespace["check_rust_toolchain"], versions


@pytest.mark.parametrize("tool", ["rustc", "cargo"])
@pytest.mark.parametrize("version", ["1.87.0", "1.99.0", "1.100.0", "2.0.0", "1.100.0-nightly", "1.100.0-beta.1"])
def test_accepts_supported_toolchains(check_toolchain, tool, version):
    check, versions = check_toolchain
    versions[tool] = version
    check()


@pytest.mark.parametrize("tool", ["rustc", "cargo"])
@pytest.mark.parametrize("version", ["0.99.0", "1.9.9", "1.86.100", "1.86.9"])
def test_rejects_toolchains_below_minimum(check_toolchain, tool, version):
    check, versions = check_toolchain
    versions[tool] = version
    with pytest.raises(OSError, match=f"{tool} version 1.87.0 or later required, {version} found"):
        check()
