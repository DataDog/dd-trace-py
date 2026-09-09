import ast
import os
from pathlib import Path

import pytest

from ddtrace.internal import _libddwaf_platform as layout
from ddtrace.internal.settings.asm import build_libddwaf_filename


HERE = Path(__file__).resolve()
SETUP_PY = HERE.parents[2] / "setup.py"


def _setup_py_literal(class_name, attribute):
    tree = ast.parse(SETUP_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for statement in node.body:
                targets = getattr(statement, "targets", [])
                if targets and getattr(targets[0], "id", None) == attribute:
                    return ast.literal_eval(statement.value)
    raise AssertionError("%s.%s not found in setup.py" % (class_name, attribute))


def _bundled_library(libddwaf_dir, arch, system="Linux"):
    lib_dir = Path(layout.library_dir(str(libddwaf_dir), arch))
    lib_dir.mkdir(parents=True)
    library = lib_dir / layout.library_name(system)
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
    "system,expected",
    [("Linux", "libddwaf.so"), ("Darwin", "libddwaf.dylib"), ("Windows", "libddwaf.dll")],
)
def test_library_name(system, expected):
    assert layout.library_name(system) == expected


@pytest.mark.parametrize(
    "version,usable",
    [
        ("2.0.0", True),
        ("2.0.1", True),
        ("2.3.10", True),
        ("2.1.0-rc1", True),
        ("1.20.1", False),
        ("3.0.0", False),
        ("0.9.0", False),
        ("2.0", False),
        ("2.0.x", False),
        ("", False),
        ("not a version", False),
    ],
)
def test_abi_error(version, usable):
    assert (layout.abi_error(version) is None) is usable


def test_bundled_library_is_used(tmp_path):
    library = _bundled_library(tmp_path, "x86_64")

    assert layout.resolve_library_path(str(tmp_path), "Linux", "x86_64") == str(library)


def test_recorded_path_is_used_when_nothing_is_bundled(tmp_path):
    link_file = layout.stage_system_library(str(tmp_path), "x86_64", "/usr/lib64/libddwaf.so.2")

    assert Path(link_file).read_text() == "/usr/lib64/libddwaf.so.2\n"
    assert layout.resolve_library_path(str(tmp_path), "Linux", "x86_64") == "/usr/lib64/libddwaf.so.2"


def test_bundled_path_is_returned_when_the_build_produced_nothing(tmp_path):
    resolved = layout.resolve_library_path(str(tmp_path), "Linux", "x86_64")

    assert resolved == os.path.join(str(tmp_path), "x86_64", "lib", "libddwaf.so")


def test_empty_recorded_path_is_ignored(tmp_path):
    lib_dir = Path(layout.library_dir(str(tmp_path), "x86_64"))
    lib_dir.mkdir(parents=True)
    (lib_dir / layout.LINK_FILE_NAME).write_text("\n")

    assert layout.resolve_library_path(str(tmp_path), "Linux", "x86_64") == str(lib_dir / "libddwaf.so")


def test_staging_a_system_library_drops_bundled_libraries(tmp_path):
    _bundled_library(tmp_path, "x86_64")
    _bundled_library(tmp_path, "aarch64")

    layout.stage_system_library(str(tmp_path), "x86_64", "/usr/lib64/libddwaf.so.2")

    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [layout.LINK_FILE_NAME]
    assert [p.name for p in tmp_path.iterdir()] == ["x86_64"]


def test_removing_recorded_paths_leaves_bundled_libraries(tmp_path):
    layout.stage_system_library(str(tmp_path), "x86_64", "/usr/lib64/libddwaf.so.2")
    library = _bundled_library(tmp_path, "aarch64")

    layout.remove_link_files(str(tmp_path))

    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [library.name]
    assert layout.read_link_file(layout.library_dir(str(tmp_path), "x86_64")) is None


def test_removing_recorded_paths_without_artifacts(tmp_path):
    layout.remove_link_files(str(tmp_path / "missing"))


def test_the_installed_library_is_abi_compatible():
    ddwaf_types = pytest.importorskip("ddtrace.appsec._ddwaf.ddwaf_types")

    version = ddwaf_types.ddwaf_get_version().decode()

    assert layout.abi_error(version) is None, version
    assert os.path.exists(build_libddwaf_filename())
