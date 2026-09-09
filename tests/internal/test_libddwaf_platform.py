import ast
import os
from pathlib import Path

import pytest

from ddtrace.internal import _libddwaf_platform as layout
from ddtrace.internal.settings.asm import build_libddwaf_filename


SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _setup_py_literal(class_name, attribute):
    tree = ast.parse(SETUP_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for statement in node.body:
                targets = getattr(statement, "targets", [])
                if targets and getattr(targets[0], "id", None) == attribute:
                    return ast.literal_eval(statement.value)
    raise AssertionError("%s.%s not found in setup.py" % (class_name, attribute))


def _bundle(libddwaf_dir, arch, system="Linux"):
    lib_dir = libddwaf_dir / arch / "lib"
    lib_dir.mkdir(parents=True)
    library = lib_dir / layout.bundled_library_name(system)
    library.write_text("")
    return library


@pytest.mark.parametrize(
    "system,machine,is_64bit,expected",
    [
        ("Linux", "x86_64", True, "x86_64"),
        ("Linux", "aarch64", True, "aarch64"),
        ("Linux", "amd64", True, "x64"),
        ("Linux", "i686", True, "x86_64"),
        ("Darwin", "arm64", True, "arm64"),
        ("Darwin", "x86_64", True, "x86_64"),
        ("Windows", "AMD64", True, "x64"),
        ("Windows", "AMD64", False, "win32"),
        ("Windows", "ARM64", True, "arm64"),
        ("Windows", "x86", True, "win32"),
    ],
)
def test_target_arch(system, machine, is_64bit, expected):
    assert layout.target_arch(system, machine, is_64bit) == expected


def test_target_arch_matches_the_directories_the_build_creates():
    releases = _setup_py_literal("LibDDWafDownload", "available_releases")
    machines = {
        "Linux": ["x86_64", "aarch64"],
        "Darwin": ["x86_64", "arm64"],
        "Windows": ["AMD64", "ARM64"],
    }
    for system, candidates in machines.items():
        for machine in candidates:
            assert layout.target_arch(system, machine) in releases[system]
    assert layout.target_arch("Windows", "AMD64", False) in releases["Windows"]


@pytest.mark.parametrize(
    "system,bundled,soname",
    [
        ("Linux", "libddwaf.so", "libddwaf.so.2"),
        ("Darwin", "libddwaf.dylib", "libddwaf.2.dylib"),
        ("Windows", "libddwaf.dll", None),
    ],
)
def test_library_names(system, bundled, soname):
    assert layout.bundled_library_name(system) == bundled
    assert layout.system_library_name(system) == soname


def test_the_bundled_library_wins(tmp_path):
    library = _bundle(tmp_path, "x86_64")

    assert layout.resolve_library(str(tmp_path), "Linux", "x86_64") == str(library)


def test_the_soname_is_used_when_nothing_is_bundled(tmp_path):
    resolved = layout.resolve_library(str(tmp_path), "Linux", "x86_64")

    assert resolved == "libddwaf.so.2"
    assert layout.is_loadable(resolved)


def test_a_bundled_library_for_another_architecture_is_ignored(tmp_path):
    _bundle(tmp_path, "aarch64")

    assert layout.resolve_library(str(tmp_path), "Linux", "x86_64") == "libddwaf.so.2"


def test_the_bundled_path_is_returned_where_there_is_no_soname(tmp_path):
    resolved = layout.resolve_library(str(tmp_path), "Windows", "AMD64")

    assert resolved == os.path.join(str(tmp_path), "x64", "lib", "libddwaf.dll")
    assert not layout.is_loadable(resolved)


def test_a_missing_bundled_library_is_not_loadable(tmp_path):
    assert not layout.is_loadable(str(tmp_path / "x86_64" / "lib" / "libddwaf.so"))


def test_the_installed_library_loads():
    ddwaf_types = pytest.importorskip("ddtrace.appsec._ddwaf.ddwaf_types")

    version = ddwaf_types.ddwaf_get_version().decode()

    assert version.startswith("%d." % layout.ABI_MAJOR), version
    assert layout.is_loadable(build_libddwaf_filename())
