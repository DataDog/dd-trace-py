"""Unit tests for the checksum verification of the libddwaf archives downloaded by setup.py."""

import ast
import contextlib
import hashlib
import io
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import typing as t

import pytest


SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"
LIBRARY = b"genuine libddwaf"


def _definition_name(node):
    if isinstance(node, ast.Assign):
        return getattr(node.targets[0], "id", None)
    return getattr(node, "name", None)


def _setup_py_source(*names):
    source = SETUP_PY.read_text()
    segments = [ast.get_source_segment(source, n) or "" for n in ast.parse(source).body if _definition_name(n) in names]
    assert len(segments) == len(names), names
    return "\n\n".join(segments)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup_py_download(here, urlretrieve=None):
    """Exec the libddwaf download of setup.py as a Linux x86_64 build would run it."""
    namespace = {
        "contextlib": contextlib,
        "hashlib": hashlib,
        "io": io,
        "os": os,
        "platform": platform,
        "shutil": shutil,
        "tarfile": tarfile,
        "tempfile": tempfile,
        "time": time,
        "t": t,
        "Path": Path,
        "CURRENT_OS": "Linux",
        "HERE": here,
        "LIBDDWAF_DOWNLOAD_DIR": here / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf",
        "get_platform": lambda: "linux-x86_64",
        "is_64_bit_python": lambda: True,
        "retry_download": lambda: lambda f: f,
        "urlretrieve": urlretrieve,
        "DebugMetadata": type("DebugMetadata", (), {"download_times": {}}),
    }
    exec(  # noqa: S102
        _setup_py_source(
            "LIBDDWAF_VERSION",
            "LIBDDWAF_CHECKSUMS",
            "checksum_mismatch",
            "LibraryDownload",
            "LibDDWafDownload",
        ),
        namespace,
    )
    return namespace


def _libddwaf_download(tmp_path):
    """The libddwaf download of setup.py, fetching from a stub release."""
    release = tmp_path / "release"
    release.mkdir()
    downloads = []

    def urlretrieve(url, dest):
        downloads.append(Path(dest))
        shutil.copyfile(release / url.rsplit("/", 1)[-1], dest)
        return str(dest), None

    download = _setup_py_download(tmp_path / "source", urlretrieve)["LibDDWafDownload"]
    download.CACHE_DIR = tmp_path / "cache"
    download.USE_CACHE = True

    archive_name = download.get_archive_name("x86_64", "Linux")
    archive = release / archive_name
    _write_archive(archive, download.get_package_name("x86_64", "Linux"), LIBRARY)
    download.expected_checksums = {archive_name: _sha256(archive)}
    return download, archive, downloads


def _write_archive(path, package_name, library):
    with tarfile.open(path, "w:gz") as tar:
        member = tarfile.TarInfo("%s/lib/libddwaf.so" % package_name)
        member.size = len(library)
        tar.addfile(member, io.BytesIO(library))


def _tamper(download, archive):
    _write_archive(archive, download.get_package_name("x86_64", "Linux"), b"tampered")


def _bundled_library(download):
    return Path(download.download_dir) / "x86_64" / "lib" / "libddwaf.so"


def _cached(download, archive):
    return download.CACHE_DIR / archive.name


def test_every_release_archive_has_a_pinned_checksum(tmp_path):
    namespace = _setup_py_download(tmp_path)
    download = namespace["LibDDWafDownload"]
    checksums = namespace["LIBDDWAF_CHECKSUMS"]

    archives = {
        download.get_archive_name(arch, system)
        for system, architectures in download.available_releases.items()
        for arch in architectures
    }

    assert download.expected_checksums is checksums
    assert set(checksums) == archives
    assert all(re.fullmatch("[0-9a-f]{64}", checksum) for checksum in checksums.values())


def test_a_downloaded_archive_matching_its_checksum_is_bundled(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)

    download.run()

    assert len(downloads) == 1
    assert _bundled_library(download).read_bytes() == LIBRARY
    assert _cached(download, archive).read_bytes() == archive.read_bytes()


def test_a_download_reaches_the_cache_only_once_verified(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)

    download.run()

    [partial] = downloads
    assert partial.parent == download.CACHE_DIR
    assert partial != _cached(download, archive)
    assert list(download.CACHE_DIR.iterdir()) == [_cached(download, archive)]


def test_a_tampered_download_fails_the_build_and_is_not_cached(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)
    _tamper(download, archive)

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        download.run()

    assert len(downloads) == 1
    assert list(download.CACHE_DIR.iterdir()) == []
    assert not _bundled_library(download).exists()


def test_a_mismatch_is_reported_when_the_rejected_download_cannot_be_deleted(tmp_path, monkeypatch):
    download, archive, _ = _libddwaf_download(tmp_path)
    _tamper(download, archive)

    def unlink(path):
        raise PermissionError(path)

    monkeypatch.setattr(os, "unlink", unlink)

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        download.run()

    assert not _cached(download, archive).exists()


def test_a_cached_archive_matching_its_checksum_is_not_downloaded_again(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)
    download.CACHE_DIR.mkdir()
    shutil.copyfile(archive, _cached(download, archive))

    download.run()

    assert downloads == []
    assert _bundled_library(download).read_bytes() == LIBRARY


def test_a_tampered_cached_archive_is_downloaded_again(tmp_path, capsys):
    download, archive, downloads = _libddwaf_download(tmp_path)
    download.CACHE_DIR.mkdir()
    _write_archive(_cached(download, archive), download.get_package_name("x86_64", "Linux"), b"tampered")

    download.run()

    assert len(downloads) == 1
    assert _bundled_library(download).read_bytes() == LIBRARY
    assert _cached(download, archive).read_bytes() == archive.read_bytes()
    output = capsys.readouterr().out
    assert "Discarding the cached archive. Checksum mismatch" in output
    assert download.expected_checksums[archive.name] in output


def test_without_the_download_cache_no_archive_is_left_behind(tmp_path, monkeypatch):
    download, _, downloads = _libddwaf_download(tmp_path)
    download.USE_CACHE = False
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    download.run()

    assert len(downloads) == 1
    assert _bundled_library(download).read_bytes() == LIBRARY
    assert list(workdir.iterdir()) == []
    assert not download.CACHE_DIR.exists()


def test_a_library_not_extracted_from_a_pinned_archive_is_replaced(tmp_path):
    download, _, downloads = _libddwaf_download(tmp_path)
    library = _bundled_library(download)
    library.parent.mkdir(parents=True)
    library.write_bytes(b"unverified libddwaf")
    (Path(download.download_dir) / ".version").write_text(download.version)

    download.run()

    assert len(downloads) == 1
    assert library.read_bytes() == LIBRARY


def test_a_library_extracted_from_a_pinned_archive_is_reused(tmp_path):
    download, _, downloads = _libddwaf_download(tmp_path)
    download.run()
    shutil.rmtree(download.CACHE_DIR)

    download.run()

    assert len(downloads) == 1
    assert _bundled_library(download).read_bytes() == LIBRARY


def test_an_archive_without_a_pinned_checksum_is_not_downloaded(tmp_path):
    download, _, downloads = _libddwaf_download(tmp_path)
    download.expected_checksums = {}

    with pytest.raises(RuntimeError, match="No pinned checksum"):
        download.run()

    assert downloads == []


def test_a_tampered_download_fails_the_build_under_python_optimizations(tmp_path):
    script = "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "from tests.internal import test_setup_libddwaf_checksum as harness",
            "if not sys.flags.optimize:",
            "    sys.exit('not running under -O')",
            "download, archive, _ = harness._libddwaf_download(Path(sys.argv[1]))",
            "harness._tamper(download, archive)",
            "download.run()",
        ]
    )

    result = subprocess.run(
        [sys.executable, "-O", "-c", script, str(tmp_path)], cwd=SETUP_PY.parent, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stderr, result.stderr
    assert list((tmp_path / "cache").iterdir()) == []
