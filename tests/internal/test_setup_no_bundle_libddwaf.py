"""Unit tests for the build_py --no-bundle-libddwaf option of setup.py."""

import os
from pathlib import Path
import shutil

from ddtrace.internal import _libddwaf_platform as layout


SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _library_downloader(tmp_path):
    """Exec the build_py subclass of setup.py with a stub setuptools base."""
    source = SETUP_PY.read_text()
    code = source[source.index("class LibraryDownloader(BuildPyCommand):") : source.index("class CleanLibraries(")]
    here = tmp_path / "source"
    (here / "ddtrace").mkdir(parents=True)
    libddwaf_dir = here / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
    downloads = []

    class StubBuildPy:
        """Copies into build_lib the way setuptools does: never removing anything."""

        editable_mode = False
        user_options = []
        boolean_options = []

        def initialize_options(self):
            pass

        def run(self):
            shutil.copytree(here / "ddtrace", Path(self.build_lib) / "ddtrace", dirs_exist_ok=True)

    namespace = {
        "os": os,
        "shutil": shutil,
        "Path": Path,
        "BuildPyCommand": StubBuildPy,
        "CustomBuildExt": type("CustomBuildExt", (), {"INCREMENTAL": True}),
        "CleanLibraries": type("CleanLibraries", (), {"remove_artifacts": staticmethod(lambda: None)}),
        "LibDDWafDownload": type("LibDDWafDownload", (), {"run": staticmethod(lambda: downloads.append(1))}),
        "HERE": here,
        "LIBDDWAF_DOWNLOAD_DIR": libddwaf_dir,
        "IS_EDITABLE": False,
        "_WHEEL_EXCLUDED_EXTENSIONS": frozenset([".c"]),
    }
    exec(code, namespace)  # noqa: S102
    downloader = namespace["LibraryDownloader"]()
    downloader.initialize_options()
    downloader.build_lib = str(tmp_path / "build" / "lib")
    return downloader, libddwaf_dir, downloads


def _bundled_library(libddwaf_dir, arch="aarch64"):
    lib_dir = libddwaf_dir / arch / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    library = lib_dir / "libddwaf.so"
    library.write_text("")
    return library


def _staged(downloader):
    staged = Path(downloader.build_lib) / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
    return sorted(p.name for p in staged.rglob("*") if p.is_file())


def test_the_option_is_off_by_default(tmp_path):
    downloader, libddwaf_dir, downloads = _library_downloader(tmp_path)
    _bundled_library(libddwaf_dir)

    downloader.run()

    assert downloads == [1]
    assert _staged(downloader) == ["libddwaf.so"]


def test_the_option_skips_the_download_and_bundles_nothing(tmp_path):
    downloader, libddwaf_dir, downloads = _library_downloader(tmp_path)
    _bundled_library(libddwaf_dir)
    downloader.no_bundle_libddwaf = 1

    downloader.run()

    assert downloads == []
    assert not libddwaf_dir.exists()
    assert _staged(downloader) == []


def test_the_option_is_declared_as_a_boolean_build_py_option(tmp_path):
    downloader, _, _ = _library_downloader(tmp_path)

    assert ("no-bundle-libddwaf", None) == type(downloader).user_options[-1][:2]
    assert "no-bundle-libddwaf" in type(downloader).boolean_options
    assert downloader.no_bundle_libddwaf == 0


def test_a_bundled_library_is_not_staged_again_after_switching(tmp_path):
    downloader, libddwaf_dir, _ = _library_downloader(tmp_path)
    _bundled_library(libddwaf_dir)
    downloader.run()
    assert _staged(downloader) == ["libddwaf.so"]

    downloader.no_bundle_libddwaf = 1
    downloader.run()

    assert _staged(downloader) == []
    assert layout.resolve_library(str(Path(downloader.build_lib) / "x"), "Linux", "aarch64") == "libddwaf.so.2"


def test_the_bundled_library_comes_back_when_the_option_is_dropped(tmp_path):
    downloader, libddwaf_dir, _ = _library_downloader(tmp_path)
    downloader.no_bundle_libddwaf = 1
    downloader.run()
    assert _staged(downloader) == []

    downloader.no_bundle_libddwaf = 0
    _bundled_library(libddwaf_dir)
    downloader.run()

    assert _staged(downloader) == ["libddwaf.so"]
