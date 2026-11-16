"""ddtrace.internal.packages against importlib.metadata, on the same installs.

The installs follow CPython's own importlib.metadata fixtures
(Lib/test/test_importlib/metadata/fixtures.py), with zip variants of them and
some broken metadata. Each is checked on its own and all of them together, for
discovery order and shadowing.
"""

from __future__ import annotations

from importlib.machinery import PathFinder
import importlib.metadata as importlib_metadata
from pathlib import Path
import sys
import textwrap
import typing as t
import zipfile

import pytest


# A file's content, or a directory's files by name.
_Files = dict[str, t.Union[str, "_Files"]]


def _build(files: _Files, root: Path) -> None:
    for name, content in files.items():
        path = root / name
        if isinstance(content, dict):
            path.mkdir(parents=True, exist_ok=True)
            _build(content, path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content).lstrip())


def _zip(files: _Files, archive: Path, compression: int = zipfile.ZIP_DEFLATED, metadata_compression=None) -> None:
    """files as a zip; metadata_compression for the members of metadata directories."""

    def members(files: _Files, prefix: str = "") -> t.Iterator[tuple[str, str]]:
        for name, content in files.items():
            if isinstance(content, dict):
                yield from members(content, f"{prefix}{name}/")
            else:
                yield prefix + name, textwrap.dedent(content).lstrip()

    with zipfile.ZipFile(archive, "w", compression) as zf:
        for name, content in members(files):
            top = name.split("/", 1)[0]
            in_metadata = top.endswith((".dist-info", ".egg-info")) or top == "EGG-INFO"
            zf.writestr(name, content, compress_type=metadata_compression if in_metadata else None)


DIST_INFO: _Files = {
    "distinfo_pkg-1.0.0.dist-info": {
        "METADATA": """
            Name: distinfo-pkg
            Author: Steven Ma
            Version: 1.0.0
            Requires-Dist: wheel >= 1.0
            Keywords: sample package

            Once upon a time
            There was a distinfo pkg
            """,
        "RECORD": "mod.py,sha256=abc,20\n",
    },
    "mod.py": "def main(): ...\n",
}

DIST_INFO_WITH_DOT: _Files = {
    "pkg_dot-1.0.0.dist-info": {"METADATA": "Name: pkg.dot\nVersion: 1.0.0\n"},
}

DIST_INFO_WITH_DOT_LEGACY: _Files = {
    "pkg.dot-1.0.0.dist-info": {"METADATA": "Name: pkg.dot\nVersion: 1.0.0\n"},
    "pkg.lot.egg-info": {"METADATA": "Name: pkg.lot\nVersion: 1.0.0\n"},
}

EGG_INFO: _Files = {
    "egginfo_pkg.egg-info": {
        "PKG-INFO": """
            Name: egginfo-pkg
            Author: Steven Ma
            License: Unknown
            Version: 1.0.0
            Description: Once upon a time
                    There was an egginfo package
            """,
        "SOURCES.txt": "mod.py\negginfo_pkg.egg-info/top_level.txt\n",
        "top_level.txt": "mod\n",
    },
    "mod.py": "def main(): ...\n",
}

EGG_INFO_PIP_NO_TOP_LEVEL: _Files = {
    "egg_with_module_pkg.egg-info": {
        "PKG-INFO": "Name: egg_with_module-pkg\n",
        # Made from the source archive: lists files that are not installed.
        "SOURCES.txt": """
            egg_with_module.py
            setup.py
            egg_with_module_pkg.egg-info/PKG-INFO
            egg_with_module_pkg.egg-info/SOURCES.txt
            """,
        # Written by pip, and more accurate than SOURCES.txt.
        "installed-files.txt": """
            ../../../etc/jupyter/jupyter_notebook_config.d/relative.json
            /etc/jupyter/jupyter_notebook_config.d/absolute.json
            ../egg_with_module.py
            PKG-INFO
            SOURCES.txt
            """,
    },
    "egg_with_module.py": "def main(): ...\n",
}

EGG_INFO_PIP_NO_MODULES: _Files = {
    "egg_with_no_modules_pkg.egg-info": {
        "PKG-INFO": "Name: egg_with_no_modules-pkg\n",
        "SOURCES.txt": "setup.py\negg_with_no_modules_pkg.egg-info/PKG-INFO\n",
        "installed-files.txt": "PKG-INFO\nSOURCES.txt\ntop_level.txt\n",
        "top_level.txt": "\n",
    },
}

EGG_INFO_SOURCES_FALLBACK: _Files = {
    "sources_fallback_pkg.egg-info": {
        "PKG-INFO": "Name: sources_fallback-pkg\n",
        "SOURCES.txt": "sources_fallback.py\nsetup.py\nsources_fallback_pkg.egg-info/PKG-INFO\n",
    },
    "sources_fallback.py": "def main(): ...\n",
}

EGG_INFO_FILE: _Files = {
    "egginfo_file.egg-info": """
        Metadata-Version: 1.0
        Name: egginfo_file
        Version: 0.1
        Summary: An example package
        """,
}

PACKAGES: _Files = {
    "pkg_with_packages-2.0.dist-info": {
        "METADATA": "Name: pkg-with-packages\nVersion: 2.0\n",
        "RECORD": """
            regular/__init__.py,,
            regular/sub/__init__.py,,
            regular/sub/mod.py,,
            nspace/leaf/__init__.py,,
            flat.py,,
            gone.py,,
            pkg_with_packages-2.0.dist-info/METADATA,,
            pkg_with_packages-2.0.dist-info/RECORD,,
            """,
        "top_level.txt": "regular\nnspace\nflat\n",
    },
    "regular": {"__init__.py": "", "sub": {"__init__.py": "", "mod.py": ""}},
    "nspace": {"leaf": {"__init__.py": ""}},
    "flat.py": "",
}

BROKEN: _Files = {
    "no_metadata-1.0.dist-info": {"RECORD": ""},
    "no_name-1.0.dist-info": {"METADATA": "Version: 1.0\n"},
    "no_version-1.0.dist-info": {"METADATA": "Name: no-version\n"},
    "empty_version-1.0.dist-info": {"METADATA": "Name: empty-version\nVersion:\n"},
}

DIRECTORIES: dict[str, _Files] = {
    "dist_info": DIST_INFO,
    "dist_info_with_dot": DIST_INFO_WITH_DOT,
    "dist_info_with_dot_legacy": DIST_INFO_WITH_DOT_LEGACY,
    "egg_info": EGG_INFO,
    "egg_info_pip_no_top_level": EGG_INFO_PIP_NO_TOP_LEVEL,
    "egg_info_pip_no_modules": EGG_INFO_PIP_NO_MODULES,
    "egg_info_sources_fallback": EGG_INFO_SOURCES_FALLBACK,
    "egg_info_file": EGG_INFO_FILE,
    "packages": PACKAGES,
    "broken": BROKEN,
}

EGG: _Files = {
    "EGG-INFO": {
        "PKG-INFO": "Name: example\nVersion: 21.12\n",
        "SOURCES.txt": "example/__init__.py\nsetup.py\n",
        "top_level.txt": "example\n",
    },
    "example": {"__init__.py": ""},
}


def _make_directory(files: _Files) -> t.Callable[[Path], str]:
    def make(root: Path) -> str:
        _build(files, root)
        return str(root)

    return make


def _make_zip(files: _Files, name: str = "deps.zip", **options: t.Any) -> t.Callable[[Path], str]:
    def make(root: Path) -> str:
        root.mkdir(parents=True)
        _zip(files, root / name, **options)
        return str(root / name)

    return make


# Each install makes one sys.path entry under the directory it is given.
INSTALLS: dict[str, t.Callable[[Path], str]] = {
    **{name: _make_directory(files) for name, files in DIRECTORIES.items()},
    "egg_directory": _make_directory(EGG),
    "wheel_zip": _make_zip({**DIST_INFO, **PACKAGES}, "example-21.12-py3-none-any.whl"),
    "egg_zip": _make_zip(EGG, "example-21.12-py3.6.egg"),
    "stored_zip": _make_zip(DIST_INFO, compression=zipfile.ZIP_STORED),
    # LZMA is left out: the native reader skips it, with a warning.
    "bzip2_metadata_zip": _make_zip({**DIST_INFO, **PACKAGES}, metadata_compression=zipfile.ZIP_BZIP2),
}
# Two entries with other versions of the same distributions, for shadowing: the
# first one wins lookups, the last one the map of versions.
SHADOWED = {
    "shadowing": _make_directory(DIST_INFO),
    "shadowed": _make_zip(
        {
            "distinfo_pkg-2.0.0.dist-info": {"METADATA": "Name: distinfo-pkg\nVersion: 2.0.0\n", "RECORD": ""},
            "egginfo_pkg.egg-info": {"PKG-INFO": "Name: egginfo-pkg\nVersion: 2.0.0\n"},
        }
    ),
}


@pytest.fixture
def isolated_packages(monkeypatch: pytest.MonkeyPatch):
    """Discovery from sys.path only, with caches clean before and after."""
    from ddtrace.internal import packages

    def clear() -> None:
        packages._reset_installed_distributions()
        packages._clear_lookup_caches()

    monkeypatch.setattr(sys, "meta_path", [PathFinder])
    monkeypatch.setattr(sys, "path", [])
    clear()
    yield packages
    clear()


def _install(root: Path, installs: dict[str, t.Callable[[Path], str]]) -> None:
    sys.path[:] = [make(root / name) for name, make in installs.items()]


def _names() -> set[str]:
    """Every name to look up: the distributions' names and spellings of them, and
    the names in their metadata directories, for those without a name.
    """
    names = {"missing"}
    for dist in importlib_metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            names |= {name, name.upper(), name.replace("-", "_"), name.replace("-", ".").replace("_", ".")}
        stem = getattr(dist, "_path", Path()).name.rsplit(".", 1)[0]
        names.add(stem.split("-", 1)[0])
    return names


def _expected_version(name: str) -> str:
    try:
        return importlib_metadata.version(name) or ""
    except importlib_metadata.PackageNotFoundError:
        return ""


def _expected_available(name: str) -> bool:
    try:
        importlib_metadata.distribution(name)
    except importlib_metadata.PackageNotFoundError:
        return False
    return True


def _check_against_importlib(packages) -> None:
    # The map of versions keeps the last distribution of a name, as dict() would.
    expected_versions = {}
    for dist in importlib_metadata.distributions():
        name = dist.metadata["Name"]
        if name and dist.version:
            expected_versions[name.lower()] = dist.version
    assert dict(packages.get_distributions()) == expected_versions

    for name in sorted(_names()):
        assert packages.get_version_for_package(name) == _expected_version(name), name
        assert packages.is_distribution_available(name) == _expected_available(name), name

    if sys.version_info >= (3, 12):
        # Before 3.12, importlib infers top-level names from .py files only.
        expected_packages = {k: sorted(v) for k, v in importlib_metadata.packages_distributions().items()}
        assert {k: sorted(v) for k, v in packages.get_package_distributions().items()} == expected_packages


@pytest.mark.skipif(sys.version_info < (3, 10), reason="importlib.metadata normalizes names from Python 3.10")
@pytest.mark.parametrize("install", list(INSTALLS))
def test_install_matches_importlib(install: str, tmp_path: Path, isolated_packages) -> None:
    _install(tmp_path, {install: INSTALLS[install]})
    _check_against_importlib(isolated_packages)


@pytest.mark.skipif(sys.version_info < (3, 10), reason="importlib.metadata normalizes names from Python 3.10")
def test_installs_together_match_importlib(tmp_path: Path, isolated_packages) -> None:
    _install(tmp_path, {**INSTALLS, **SHADOWED})
    _check_against_importlib(isolated_packages)


@pytest.mark.skipif(sys.version_info < (3, 10), reason="importlib.metadata normalizes names from Python 3.10")
def test_shadowed_distributions_match_importlib(tmp_path: Path, isolated_packages) -> None:
    _install(tmp_path, SHADOWED)
    _check_against_importlib(isolated_packages)
    sys.path.reverse()
    isolated_packages._clear_lookup_caches()
    _check_against_importlib(isolated_packages)
