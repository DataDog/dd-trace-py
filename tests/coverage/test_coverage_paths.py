"""Path resolution of the coverage collector."""

import os

import pytest

from ddtrace.internal.coverage import native_lines
from ddtrace.internal.coverage import util


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX resolution goes through the posix module")


def _refuse(*args, **kwargs):
    raise AssertionError("patched function called")


@pytest.fixture
def tree(tmp_path):
    real = tmp_path / "real"
    (real / "pkg").mkdir(parents=True)
    (real / "pkg" / "mod.py").write_text("")
    os.symlink(real, tmp_path / "linked_dir")
    os.symlink(real / "pkg" / "mod.py", tmp_path / "linked_file.py")
    os.symlink("../../real/pkg", real / "pkg" / "up")
    os.symlink("linked_dir", tmp_path / "chain")
    os.symlink("loop_b", tmp_path / "loop_a")
    os.symlink("loop_a", tmp_path / "loop_b")
    os.symlink("missing", tmp_path / "dangling")
    return tmp_path


PATHS = [
    "real/pkg/mod.py",
    "linked_dir/pkg/mod.py",
    "linked_file.py",
    "real/pkg/up/mod.py",
    "linked_dir/pkg/up/up/mod.py",
    "chain/pkg/../pkg/mod.py",
    "real/./pkg//mod.py",
    "loop_a/x.py",
    "dangling",
    "dangling/x.py",
    "missing/../real/pkg/mod.py",
    "..",
    ".",
]


@pytest.mark.parametrize("relative", PATHS)
def test_realpath_matches_os_realpath(tree, relative):
    path = os.path.join(str(tree), relative)
    assert util._realpath(path) == os.path.realpath(path)


@pytest.mark.parametrize("relative", PATHS)
def test_realpath_of_relative_paths(tree, relative, monkeypatch):
    monkeypatch.chdir(tree)
    assert util._realpath(relative) == os.path.realpath(relative)


def test_resolution_ignores_patched_os(tree, monkeypatch):
    """Blocking-call detectors and fake file systems patch os while the tests import modules."""
    expected = os.path.realpath(tree / "real" / "pkg" / "mod.py")
    with monkeypatch.context() as patch:
        for name in ("lstat", "stat", "readlink", "getcwd"):
            patch.setattr(os, name, _refuse)
        for name in ("islink", "abspath", "realpath", "relpath", "exists"):
            patch.setattr(os.path, name, _refuse)
        # Paths not seen before, so the memoized results do not answer.
        results = [
            util._realpath(str(tree / "linked_dir" / "pkg" / "up" / "mod.py")),
            util.resolved_path(str(tree / "linked_file.py")),
            util.resolved_path(str(tree / "chain" / "pkg" / "mod.py")),
        ]
    assert results == [expected] * 3


def test_report_path_is_a_prefix_test(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(os.path, "abspath", _refuse)
        patch.setattr(os.path, "relpath", _refuse)
        patch.setattr(native_lines, "_report_root", "/work/repo")
        results = [
            native_lines._report_path("/work/repo/pkg/mod.py"),
            native_lines._report_path("/work/repo"),
            native_lines._report_path("/work/repo/"),
            native_lines._report_path("/work/repository/mod.py"),
            native_lines._report_path("/work/other/mod.py"),
        ]
        patch.setattr(native_lines, "_report_root", "/")
        results.append(native_lines._report_path("/pkg/mod.py"))
    assert results == ["/pkg/mod.py", None, None, None, None, "/pkg/mod.py"]
