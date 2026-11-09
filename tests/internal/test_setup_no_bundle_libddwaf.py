"""WAF build modes and migration from --no-bundle-libddwaf."""

import os
from pathlib import Path
import shutil

import pytest


SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _library_downloader(tmp_path):
    """Exec the build_py subclass of setup.py with a stub setuptools base."""
    source = SETUP_PY.read_text()
    code = source[source.index("class LibraryDownloader(BuildPyCommand):") : source.index("class CleanLibraries(")]
    here = tmp_path / "source"
    (here / "ddtrace").mkdir(parents=True)
    libddwaf_dir = here / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"

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
        "CURRENT_OS": "Linux",
        "CustomBuildExt": type("CustomBuildExt", (), {"INCREMENTAL": True}),
        "CleanLibraries": type("CleanLibraries", (), {"remove_artifacts": staticmethod(lambda: None)}),
        "HERE": here,
        "LIBDDWAF_DOWNLOAD_DIR": libddwaf_dir,
        "IS_EDITABLE": False,
        "_WHEEL_EXCLUDED_EXTENSIONS": frozenset([".c"]),
    }
    exec(code, namespace)  # noqa: S102
    downloader = namespace["LibraryDownloader"]()
    downloader.initialize_options()
    downloader.build_lib = str(tmp_path / "build" / "lib")
    return downloader, libddwaf_dir


def _bundled_library(libddwaf_dir, arch="aarch64"):
    lib_dir = libddwaf_dir / arch / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    library = lib_dir / "libddwaf.so"
    library.write_text("")
    return library


def _staged(downloader):
    staged = Path(downloader.build_lib) / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
    return sorted(p.name for p in staged.rglob("*") if p.is_file())


@pytest.mark.parametrize("mode", [None, "static", "source-static"])
def test_build_modes_use_static_libraries(tmp_path, mode):
    downloader, libddwaf_dir = _library_downloader(tmp_path)
    _bundled_library(libddwaf_dir)
    downloader.libddwaf_mode = mode

    downloader.run()

    assert _staged(downloader) == []


def test_the_option_is_declared_as_a_boolean_build_py_option(tmp_path):
    downloader, _ = _library_downloader(tmp_path)

    assert ("no-bundle-libddwaf", None) == type(downloader).user_options[-1][:2]
    assert "no-bundle-libddwaf" in type(downloader).boolean_options
    assert downloader.no_bundle_libddwaf == 0


def test_setup_cfg_sets_the_option_on_the_build_py_command(tmp_path, monkeypatch):
    """The documented way to pass the option: setuptools maps setup.cfg onto the command."""
    from setuptools.command.build_py import build_py
    from setuptools.dist import Distribution

    source = SETUP_PY.read_text()
    code = source[source.index("class LibraryDownloader(BuildPyCommand):") : source.index("class CleanLibraries(")]
    namespace = {"BuildPyCommand": build_py}
    exec(code, namespace)  # noqa: S102

    (tmp_path / "setup.cfg").write_text("[build_py]\nno_bundle_libddwaf = 1\n")
    monkeypatch.chdir(tmp_path)
    distribution = Distribution({"script_name": "setup.py", "cmdclass": {"build_py": namespace["LibraryDownloader"]}})
    distribution.parse_config_files()

    command = distribution.get_command_obj("build_py")

    assert isinstance(command, namespace["LibraryDownloader"])
    assert command.no_bundle_libddwaf is True
    with pytest.raises(RuntimeError, match="source-static"):
        command.waf_mode()


def test_invalid_mode_fails_before_removing_artifacts(tmp_path):
    downloader, directory = _library_downloader(tmp_path)
    library = _bundled_library(directory)
    downloader.libddwaf_mode = "invalid"
    with pytest.raises(RuntimeError, match="must be"):
        downloader.run()
    assert library.exists()


@pytest.mark.parametrize("mode", [None, "source-static"])
def test_no_bundle_fails_with_migration_before_removing_artifacts(tmp_path, mode):
    downloader, directory = _library_downloader(tmp_path)
    library = _bundled_library(directory)
    downloader.no_bundle_libddwaf = 1
    downloader.libddwaf_mode = mode
    with pytest.raises(RuntimeError, match="statically embeds it"):
        downloader.run()
    assert library.exists()
