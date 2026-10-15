"""Unit tests for the checksum verification of the libddwaf archives downloaded by setup.py."""

import ast
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
        "hashlib": hashlib,
        "os": os,
        "platform": platform,
        "shutil": shutil,
        "tarfile": tarfile,
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
            "sha256_checksum",
            "verify_checksum",
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
        downloads.append(url)
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


def _bundled_library(download):
    return Path(download.download_dir) / "x86_64" / "lib" / "libddwaf.so"


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
    assert (download.CACHE_DIR / archive.name).read_bytes() == archive.read_bytes()


def test_a_tampered_download_fails_the_build_and_is_not_cached(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)
    _write_archive(archive, download.get_package_name("x86_64", "Linux"), b"tampered")

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        download.run()

    assert len(downloads) == 1
    assert not (download.CACHE_DIR / archive.name).exists()
    assert not _bundled_library(download).exists()


def test_a_cached_archive_matching_its_checksum_is_not_downloaded_again(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)
    download.CACHE_DIR.mkdir()
    shutil.copyfile(archive, download.CACHE_DIR / archive.name)

    download.run()

    assert downloads == []
    assert _bundled_library(download).read_bytes() == LIBRARY


def test_a_tampered_cached_archive_is_downloaded_again(tmp_path):
    download, archive, downloads = _libddwaf_download(tmp_path)
    download.CACHE_DIR.mkdir()
    _write_archive(download.CACHE_DIR / archive.name, download.get_package_name("x86_64", "Linux"), b"tampered")

    download.run()

    assert len(downloads) == 1
    assert _bundled_library(download).read_bytes() == LIBRARY
    assert (download.CACHE_DIR / archive.name).read_bytes() == archive.read_bytes()


def test_an_archive_without_a_pinned_checksum_is_not_downloaded(tmp_path):
    download, _, downloads = _libddwaf_download(tmp_path)
    download.expected_checksums = {}

    with pytest.raises(RuntimeError, match="No pinned checksum"):
        download.run()

    assert downloads == []


def test_the_checksum_is_verified_under_python_optimizations(tmp_path):
    archive = tmp_path / "libddwaf.tar.gz"
    archive.write_bytes(b"tampered")
    script = "\n".join(
        [
            "import hashlib",
            "from pathlib import Path",
            _setup_py_source("sha256_checksum", "verify_checksum"),
            "verify_checksum(%r, %r)" % (hashlib.sha256(LIBRARY).hexdigest(), str(archive)),
        ]
    )

    result = subprocess.run([sys.executable, "-O", "-c", script], capture_output=True, text=True)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stderr
    assert not archive.exists()
