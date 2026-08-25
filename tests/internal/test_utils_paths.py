import itertools
import os
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath

import pytest

from ddtrace.internal.utils import paths
from ddtrace.internal.utils.paths import deepest_containing_root
from ddtrace.internal.utils.paths import is_contained
from ddtrace.internal.utils.paths import relative_parts
from ddtrace.internal.utils.paths import relative_path


# Deliberately includes near-misses a naive string prefix test gets wrong
# (/a/bb under /a/b, /ab under /a), the degenerate filesystem root, POSIX's
# separate '//' anchor, relative paths, and '.'.
POSIX_PATHS = [
    "/",
    "//",
    "//a",
    "//a/b",
    "/a",
    "/a/b",
    "/a/b/c",
    "/a/b/c/d.py",
    "/a/b.py",
    "/a/bb",
    "/ab",
    "/x/y",
    ".",
    "a",
    "a/b",
    "a/b/c.py",
]

POSIX_ROOTS = ["/", "//", "/a", "/a/b", "/a/bb", "/x/y", "."]


def _reference_relative_parts(path, root):
    """What relative_to would return, or None where it raises."""
    try:
        return path.relative_to(root).parts
    except ValueError:
        return None


@pytest.mark.parametrize("path_str,root_str", itertools.product(POSIX_PATHS, repeat=2))
def test_matches_pathlib(path_str, root_str):
    """relative_parts/is_contained agree with pathlib on every pair.

    pathlib is the specification here, so this is the test that matters: the
    helpers exist only to be a cheaper way of computing the same answer.
    """
    path, root = Path(path_str), Path(root_str)
    expected = _reference_relative_parts(path, root)

    assert relative_parts(path, root) == expected
    assert is_contained(path, root) is (expected is not None)


@pytest.mark.parametrize("path_str,root_str", itertools.product(POSIX_PATHS, repeat=2))
def test_relative_path_matches_pathlib(path_str, root_str):
    path, root = Path(path_str), Path(root_str)
    if _reference_relative_parts(path, root) is None:
        assert relative_path(path, root) is None
        return

    expected = str(path.relative_to(root))
    # A path relative to itself is "" here rather than pathlib's "."
    assert relative_path(path, root) == ("" if expected == "." else expected)


@pytest.mark.parametrize("path_str", POSIX_PATHS)
def test_deepest_containing_root_matches_pathlib(path_str):
    """The deepest root wins, matching a shortest-relative-path search."""
    path = Path(path_str)
    roots = [Path(_) for _ in POSIX_ROOTS]

    best = None
    for root in roots:
        relative = _reference_relative_parts(path, root)
        if relative is None:
            continue
        if best is None or len(relative) < len(best[1]):
            best = (root, relative)

    assert deepest_containing_root(path, roots) == best


def test_deepest_containing_root_prefers_site_packages():
    """The motivating case: venv and its site-packages both on sys.path."""
    venv = Path("/app/.venv")
    site_packages = Path("/app/.venv/lib/python3.12/site-packages")
    path = Path("/app/.venv/lib/python3.12/site-packages/google/cloud/storage/blob.py")

    root, parts = deepest_containing_root(path, [venv, site_packages, Path("/app")])

    assert root == site_packages
    assert parts == ("google", "cloud", "storage", "blob.py")


def test_deepest_containing_root_no_match():
    assert deepest_containing_root(Path("/somewhere/else.py"), [Path("/a"), Path("/b")]) is None


def test_deepest_containing_root_empty_roots():
    assert deepest_containing_root(Path("/a/b"), []) is None


def test_deepest_containing_root_accepts_a_generator():
    """packages.py filters sys.path inline, so roots is often a generator."""
    roots = (Path(_) for _ in ("/a", "/a/b"))

    assert deepest_containing_root(Path("/a/b/c.py"), roots) == (Path("/a/b"), ("c.py",))


def test_root_returned_unchanged():
    """The caller's own object comes back, so its I/O methods stay available."""
    root = Path("/a/b")
    returned, _ = deepest_containing_root(Path("/a/b/c.py"), [root])
    assert returned is root


def test_path_relative_to_itself_is_empty():
    assert relative_parts(Path("/a/b"), Path("/a/b")) == ()
    assert is_contained(Path("/a/b"), Path("/a/b")) is True


@pytest.mark.parametrize(
    "path_str,root_str",
    [
        ("/a/bb", "/a/b"),  # sibling sharing a prefix
        ("/ab", "/a"),
        ("/a/bcd", "/a/b"),
    ],
)
def test_prefix_must_land_on_a_component_boundary(path_str, root_str):
    assert relative_parts(Path(path_str), Path(root_str)) is None
    assert is_contained(Path(path_str), Path(root_str)) is False


def test_strings_are_rejected_rather_than_silently_mishandled():
    """The contract is PurePath, and comparing parts enforces it.

    An earlier string-prefix implementation accepted str and quietly got
    unnormalized input wrong ("/a/./b" reported as not contained). Comparing
    .parts means a str fails loudly instead, and normalization is guaranteed
    because PurePath construction has already done it.
    """
    with pytest.raises(AttributeError):
        relative_parts("/a/b/c.py", "/a/b")

    # The same path through PurePath is normalized, so it matches.
    assert relative_parts(Path("/a/./b/c.py"), Path("/a/b")) == ("c.py",)
    assert relative_parts(Path("/a//b/c.py"), Path("/a/b")) == ("c.py",)


@pytest.mark.skipif(os.name == "nt", reason="POSIX is case-sensitive")
def test_posix_is_case_sensitive():
    assert relative_parts(PurePosixPath("/A/b"), PurePosixPath("/a")) is None
    assert is_contained(PurePosixPath("/A/b"), PurePosixPath("/a")) is False


@pytest.mark.skipif(os.name == "nt", reason="'//' anchor is POSIX-specific")
def test_double_slash_is_a_separate_posix_anchor():
    """Matches pathlib, which keeps '//a' distinct from '/a'."""
    assert relative_parts(Path("//a"), Path("/")) is None
    assert relative_parts(Path("//a/b"), Path("//")) == ("a", "b")
    assert relative_parts(Path("/a/b"), Path("//")) is None


@pytest.mark.skipif(os.name == "nt", reason="'//' anchor is POSIX-specific")
def test_deepest_containing_root_handles_the_double_slash_anchor():
    roots = [Path("/"), Path("//"), Path("//a")]

    assert deepest_containing_root(Path("//a/b"), roots) == (Path("//a"), ("b",))
    assert deepest_containing_root(Path("//x"), [Path("/")]) is None
    assert deepest_containing_root(Path("//x"), [Path("//")]) == (Path("//"), ("x",))
    assert deepest_containing_root(Path("/x/y"), [Path("/")]) == (Path("/"), ("x", "y"))
    assert deepest_containing_root(Path("/x/y"), [Path("//")]) is None


# ---------------------------------------------------------------------------
# Windows. Development and CI here are POSIX-only, so the native test below
# never runs; _force_windows_rules makes the same pathlib comparison runnable
# on any host, which is the only coverage this behaviour actually gets.
# ---------------------------------------------------------------------------

# Drive roots, UNC shares, drive-relative paths, mixed separators, the
# C:\ab-under-C:\a near-miss, differing case, and 'İ' (U+0130), whose lowercase
# is two codepoints -- the case that broke an offset-based implementation.
WINDOWS_PATHS = [
    "C:\\",
    r"C:\a",
    r"C:\A",
    r"C:\a\b",
    r"C:\a\b\c.py",
    r"C:\ab",
    r"C:a",
    r"C:a\b",
    r"D:\a",
    r"\\server\share",
    r"\\server\share\a",
    r"\\server\share\a\b.py",
    r"\\other\share\a",
    r"\a",
    r"\a\b",
    "C:/a/b",
    ".",
    r"C:\İstanbul",
    r"C:\İstanbul\site-packages",
    r"C:\İstanbul\site-packages\google\cloud\storage\blob.py",
    r"C:\İ",
    r"C:\İ\x",
    r"C:\i\x",
    r"C:\Users\İb\proj",
    r"C:\Users\İb\proj\test_a.py",
]


@pytest.fixture
def _force_windows_rules(monkeypatch):
    monkeypatch.setattr(paths, "_CASE_INSENSITIVE", True)
    monkeypatch.setattr(paths, "_SEP", "\\")


@pytest.mark.parametrize("path_str,root_str", itertools.product(WINDOWS_PATHS, repeat=2))
def test_windows_semantics_match_pathlib(path_str, root_str, _force_windows_rules):
    path, root = PureWindowsPath(path_str), PureWindowsPath(root_str)
    expected = _reference_relative_parts(path, root)

    assert paths.relative_parts(path, root) == expected
    assert paths.is_contained(path, root) is (expected is not None)

    if expected is not None:
        reference = str(path.relative_to(root))
        assert paths.relative_path(path, root) == ("" if reference == "." else reference)


def test_windows_case_folding_is_length_safe(_force_windows_rules):
    r"""Regression: 'İ'.lower() is two codepoints, so offsets cannot be reused.

    Folding the whole path and then slicing the original string at an offset
    found in the folded copy truncated the first relative component whenever a
    root contained U+0130 -- silently, since containment still reported True.
    Turkish directory names ('C:\\Users\\İbrahim') hit this in normal use.
    """
    path = PureWindowsPath(r"C:\Users\İb\proj\test_a.py")

    assert paths.relative_parts(path, PureWindowsPath(r"C:\Users\İb\proj")) == ("test_a.py",)
    assert paths.relative_parts(PureWindowsPath(r"C:\İ\x"), PureWindowsPath(r"C:\İ")) == ("x",)
    assert paths.relative_path(path, PureWindowsPath(r"C:\Users\İb")) == "proj\\test_a.py"
    # Case folding still applies to the rest of the path.
    assert paths.is_contained(path, PureWindowsPath(r"c:\users\İb")) is True


def test_windows_distinguishes_drive_and_unc_anchors(_force_windows_rules):
    """Anchors are compared as whole components, so they cannot be confused."""
    assert paths.relative_parts(PureWindowsPath(r"C:\a"), PureWindowsPath(r"D:\a")) is None
    assert paths.relative_parts(PureWindowsPath(r"\\server\share\a"), PureWindowsPath(r"\\other\share")) is None
    assert paths.relative_parts(PureWindowsPath(r"\\server\share\a\b"), PureWindowsPath(r"\\server\share")) == (
        "a",
        "b",
    )
    # A drive-relative root ('C:', no root) is a different anchor from 'C:\'.
    # pathlib agreed only from 3.12 on, where anchors became strict; before that
    # it allowed the match. No caller passes a non-absolute root.
    assert paths.relative_parts(PureWindowsPath(r"C:\a"), PureWindowsPath("C:")) is None


@pytest.mark.skipif(os.name != "nt", reason="native Windows only")
def test_windows_native_case_insensitivity():
    """The one thing the simulation cannot check: the platform probe itself."""
    assert paths._CASE_INSENSITIVE is True
    assert paths.is_contained(Path(r"C:\a\b"), Path(r"C:\A")) is True
