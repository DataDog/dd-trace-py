"""Unit tests for the DD_USE_SYSTEM_LIBDDWAF build path in setup.py."""

import os
from pathlib import Path
import shutil
import subprocess
import typing as t

import pytest

from ddtrace.internal import _libddwaf_platform as layout


pytestmark = pytest.mark.skipif(os.name == "nt", reason="system libddwaf builds are Linux only")

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _download_classes(tmp_path, use_system):
    """Exec the LibraryDownload classes of setup.py in isolation."""
    source = SETUP_PY.read_text()
    code = source[source.index("class LibraryDownload:") : source.index("# Source/build file extensions")]
    namespace = {
        "os": os,
        "shutil": shutil,
        "subprocess": subprocess,
        "t": t,
        "Path": Path,
        "CURRENT_OS": "Linux",
        "HERE": tmp_path,
        "LIBDDWAF_DOWNLOAD_DIR": tmp_path / "libddwaf",
        "LIBDDWAF_VERSION": "2.0.1",
        "USE_SYSTEM_LIBDDWAF": use_system,
        "libddwaf_platform": layout,
        "get_platform": lambda: "linux-x86_64",
        "is_64_bit_python": lambda: True,
    }
    exec(code, namespace)  # noqa: S102
    return namespace["LibDDWafDownload"]


def _fake_pkg_config(tmp_path, monkeypatch, libdir="", modversion="2.0.1", exit_code=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "pkg-config"
    script.write_text(
        "#!/bin/sh\n"
        "if [ $# -gt 0 ] && [ %d -ne 0 ]; then echo 'No package libddwaf found' >&2; exit %d; fi\n"
        'case "$1" in\n'
        "  --variable=libdir) echo '%s';;\n"
        "  --modversion) echo '%s';;\n"
        "esac\n" % (exit_code, exit_code, libdir, modversion)
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return script


def _system_library(tmp_path, name="libddwaf.so.2"):
    libdir = tmp_path / "usr" / "lib64"
    libdir.mkdir(parents=True, exist_ok=True)
    real = libdir / name
    real.write_text("")
    (libdir / "libddwaf.so").symlink_to(real)
    return libdir, real


def _bundled_library(libddwaf_dir, arch="x86_64"):
    lib_dir = Path(layout.library_dir(str(libddwaf_dir), arch))
    lib_dir.mkdir(parents=True, exist_ok=True)
    library = lib_dir / "libddwaf.so"
    library.write_text("")
    return library


def test_clean_system_library_build(tmp_path, monkeypatch):
    libdir, real = _system_library(tmp_path)
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(libdir))
    download = _download_classes(tmp_path, use_system=True)

    download.run()

    libddwaf_dir = tmp_path / "libddwaf"
    assert layout.read_link_file(layout.library_dir(str(libddwaf_dir), "x86_64")) == str(real)
    assert (libddwaf_dir / ".version").read_text() == "system"
    assert sorted(p.name for p in libddwaf_dir.rglob("*") if p.is_file()) == [".version", layout.LINK_FILE_NAME]


def test_bundled_to_system_rebuild_drops_the_bundled_library(tmp_path, monkeypatch):
    libdir, real = _system_library(tmp_path)
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(libdir))
    download = _download_classes(tmp_path, use_system=True)
    bundled = _bundled_library(download.download_dir)
    (Path(download.download_dir) / ".version").write_text("2.0.1")

    download.run()

    assert not bundled.exists()
    assert layout.read_link_file(layout.library_dir(str(download.download_dir), "x86_64")) == str(real)


def test_system_to_bundled_rebuild_drops_the_recorded_path(tmp_path, monkeypatch):
    download = _download_classes(tmp_path, use_system=False)
    layout.stage_system_library(str(download.download_dir), "x86_64", "/usr/lib64/libddwaf.so.2")
    (Path(download.download_dir) / ".version").write_text("system")
    downloaded = []
    download.download_artifacts = classmethod(lambda cls: downloaded.append(cls))

    download.run()

    assert downloaded
    assert layout.read_link_file(layout.library_dir(str(download.download_dir), "x86_64")) is None


def test_a_mismatched_version_is_only_a_warning(tmp_path, monkeypatch, capsys):
    libdir, real = _system_library(tmp_path)
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(libdir), modversion="2.3.0")
    download = _download_classes(tmp_path, use_system=True)

    download.run()

    assert "does not match the version 2.0.1 pinned by ddtrace" in capsys.readouterr().out
    assert layout.read_link_file(layout.library_dir(str(download.download_dir), "x86_64")) == str(real)


@pytest.mark.parametrize("modversion", ["1.20.1", "4.0.0", "garbage"])
def test_an_incompatible_version_is_rejected(tmp_path, monkeypatch, modversion):
    libdir, _ = _system_library(tmp_path)
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(libdir), modversion=modversion)
    download = _download_classes(tmp_path, use_system=True)

    with pytest.raises(RuntimeError, match="not usable"):
        download.run()

    assert not Path(download.download_dir).exists()


def test_a_missing_pkg_config_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    download = _download_classes(tmp_path, use_system=True)

    with pytest.raises(RuntimeError, match="requires pkg-config"):
        download.run()


def test_an_unknown_package_is_reported(tmp_path, monkeypatch):
    _fake_pkg_config(tmp_path, monkeypatch, exit_code=1)
    download = _download_classes(tmp_path, use_system=True)

    with pytest.raises(RuntimeError, match="pkg-config could not query libddwaf"):
        download.run()


def test_a_missing_library_is_reported(tmp_path, monkeypatch):
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(tmp_path / "usr" / "lib64"))
    download = _download_classes(tmp_path, use_system=True)

    with pytest.raises(FileNotFoundError, match="system libddwaf not found"):
        download.run()


def test_other_platforms_are_rejected(tmp_path, monkeypatch):
    libdir, _ = _system_library(tmp_path)
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(libdir))
    download = _download_classes(tmp_path, use_system=True)
    download.run.__func__.__globals__["CURRENT_OS"] = "Darwin"

    with pytest.raises(RuntimeError, match="only supported on Linux"):
        download.run()


def test_a_single_target_architecture_is_required(tmp_path, monkeypatch):
    libdir, _ = _system_library(tmp_path)
    _fake_pkg_config(tmp_path, monkeypatch, libdir=str(libdir))
    download = _download_classes(tmp_path, use_system=True)
    download.run.__func__.__globals__["get_platform"] = lambda: "linux-armv7l"

    with pytest.raises(RuntimeError, match="exactly one target architecture"):
        download.run()


def _library_downloader(tmp_path):
    """Exec the build_py subclass of setup.py with a stub setuptools base."""
    source = SETUP_PY.read_text()
    code = source[source.index("class LibraryDownloader(BuildPyCommand):") : source.index("class CleanLibraries(")]
    here = tmp_path / "source"
    libddwaf_dir = here / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"

    class StubBuildPy:
        """Copies into build_lib the way setuptools does: never removing anything."""

        editable_mode = False

        def run(self):
            shutil.copytree(here / "ddtrace", Path(self.build_lib) / "ddtrace", dirs_exist_ok=True)

    namespace = {
        "os": os,
        "shutil": shutil,
        "Path": Path,
        "BuildPyCommand": StubBuildPy,
        "CustomBuildExt": type("CustomBuildExt", (), {"INCREMENTAL": True}),
        "CleanLibraries": type("CleanLibraries", (), {"remove_artifacts": staticmethod(lambda: None)}),
        "LibDDWafDownload": type("LibDDWafDownload", (), {"run": staticmethod(lambda: None)}),
        "HERE": here,
        "LIBDDWAF_DOWNLOAD_DIR": libddwaf_dir,
        "IS_EDITABLE": False,
        "_WHEEL_EXCLUDED_EXTENSIONS": frozenset([".c"]),
    }
    exec(code, namespace)  # noqa: S102
    downloader = namespace["LibraryDownloader"]()
    downloader.build_lib = str(tmp_path / "build" / "lib")
    return downloader, libddwaf_dir


def _staged_libddwaf(downloader):
    staged = Path(downloader.build_lib) / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
    return sorted(p.name for p in staged.rglob("*") if p.is_file())


def test_a_bundled_library_is_not_staged_again_after_switching_to_system(tmp_path):
    downloader, libddwaf_dir = _library_downloader(tmp_path)
    _bundled_library(libddwaf_dir, "aarch64")
    downloader.run()
    assert _staged_libddwaf(downloader) == ["libddwaf.so"]

    layout.stage_system_library(str(libddwaf_dir), "aarch64", "/usr/lib64/libddwaf.so.2")
    downloader.run()

    assert _staged_libddwaf(downloader) == [layout.LINK_FILE_NAME]


def test_a_recorded_path_is_not_staged_again_after_switching_to_bundled(tmp_path):
    downloader, libddwaf_dir = _library_downloader(tmp_path)
    layout.stage_system_library(str(libddwaf_dir), "aarch64", "/usr/lib64/libddwaf.so.2")
    downloader.run()
    assert _staged_libddwaf(downloader) == [layout.LINK_FILE_NAME]

    shutil.rmtree(libddwaf_dir)
    _bundled_library(libddwaf_dir, "aarch64")
    downloader.run()

    assert _staged_libddwaf(downloader) == ["libddwaf.so"]
