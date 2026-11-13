#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "packaging>=24,<27",
#     "wheel>=0.42,<1",
# ]
# ///
"""Tests for third-party-wheels.py that do not need a manylinux image, Depot or adms."""

import base64
import csv
import hashlib
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import importlib.util
import io
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
from typing import Any
import unittest
from unittest import mock
import zipfile

from packaging.tags import Tag


SCRIPT = Path(__file__).with_name("third-party-wheels.py")
JOBS = SCRIPT.parents[1] / "third-party-wheels.yml"
SPEC = importlib.util.spec_from_file_location("third_party_wheels", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
tpw = importlib.util.module_from_spec(SPEC)
# dataclasses look up the module of the class in sys.modules.
sys.modules[SPEC.name] = tpw
SPEC.loader.exec_module(tpw)

TAG = Tag("cp315", "cp315", "manylinux_2_17_x86_64")
DEPOT_PAGE = """<!DOCTYPE html>
<html>
  <body>
    <a href="https://depot.example/files/zope-interface/7.2/sha256:0/zope.interface-7.2%2Bdd.repackaging.1-cp315-cp315-manylinux2014_x86_64.manylinux_2_17_x86_64.whl#sha256=0">x</a>
    <br />
    <a href="https://depot.example/files/zope-interface/7.2/sha256:1/zope_interface-7.2-cp313-cp313-manylinux_2_17_x86_64.whl#sha256=1">y</a>
    <a href="../../files/zope.interface-7.2.tar.gz#sha256=2">zope.interface-7.2.tar.gz</a>
  </body>
</html>
"""


def write_wheel(directory: Path, name: str = "demo_pkg", version: str = "1.0") -> Path:
    """Write a small platform wheel with a .data directory and a RECORD file."""
    tag = "cp315-cp315-manylinux_2_17_x86_64"
    dist_info = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": b"",
        f"{name}-{version}.data/scripts/demo": b"#!python\nprint('demo')\n",
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: demo-pkg\nVersion: {version}\nSummary: Demo\n\n"
            "Description text.\nVersion: 7.7\nFrom a line that email generators change.\n"
        ).encode(),
        f"{dist_info}/WHEEL": f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: false\nTag: {tag}\n".encode(),
    }
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for arcname, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([arcname, f"sha256={digest}", len(data)])
    writer.writerow([f"{dist_info}/RECORD", "", ""])
    files[f"{dist_info}/RECORD"] = record.getvalue().encode()
    path = directory / f"{name}-{version}-{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for arcname, data in files.items():
            archive.writestr(arcname, data)
    return path


class ReadEntriesTests(unittest.TestCase):
    def read(self, content: str) -> list[Any]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "list.txt")
            path.write_text(content)
            return tpw.read_entries(path)

    def test_reads_entries_and_ignores_comments(self) -> None:
        entries = self.read(
            "# comment\n\nZope.Interface 7.2 cp315-cp315 manylinux_2_17_x86_64  # trailing comment\n"
            "numpy 1.26.4 cp313-cp313t manylinux_2_17_x86_64\n"
        )
        self.assertEqual(
            entries,
            [
                tpw.Entry("zope-interface", "7.2", "cp315-cp315", "manylinux_2_17_x86_64"),
                tpw.Entry("numpy", "1.26.4", "cp313-cp313t", "manylinux_2_17_x86_64"),
            ],
        )
        self.assertEqual(entries[0].tag, TAG)

    def test_rejects_lines_that_are_not_valid(self) -> None:
        lines = {
            "numpy 1.26.4 cp313-cp313": "expected 4 fields",
            "numpy 1.26.4+local cp313-cp313 manylinux_2_17_x86_64": "without a local version",
            "numpy one cp313-cp313 manylinux_2_17_x86_64": "Invalid version",
            "numpy 1.26.4 py3-none manylinux_2_17_x86_64": "expected a CPython tag",
        }
        for line, message in lines.items():
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, message):
                self.read(line + "\n")

    def test_rejects_duplicate_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "list.txt:2: duplicate entry"):
            self.read("zope.interface 7.2 cp315-cp315 x\nzope-interface 7.2.0 cp315-cp315 x\n")

    def test_list_file_is_valid_and_each_platform_has_a_job(self) -> None:
        entries = tpw.read_entries(tpw.LIST_FILE)
        self.assertTrue(entries)
        job_platforms = set(re.findall(r'WHEEL_PLATFORM: "([^"]+)"', JOBS.read_text()))
        self.assertLessEqual({entry.platform for entry in entries}, job_platforms)


class IndexTests(unittest.TestCase):
    def test_parse_index_page_returns_the_filenames_of_the_links(self) -> None:
        self.assertEqual(
            tpw.parse_index_page(DEPOT_PAGE),
            [
                "zope.interface-7.2+dd.repackaging.1-cp315-cp315-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                "zope_interface-7.2-cp313-cp313-manylinux_2_17_x86_64.whl",
                "zope.interface-7.2.tar.gz",
            ],
        )

    def test_find_matching_wheels_ignores_the_local_version(self) -> None:
        filenames = tpw.parse_index_page(DEPOT_PAGE) + ["not-a-wheel-name.whl"]
        match = [filenames[0]]
        stderr = mock.patch("sys.stderr", io.StringIO())
        stderr.start()
        self.addCleanup(stderr.stop)
        self.assertEqual(tpw.find_matching_wheels(filenames, "zope-interface", "7.2", [TAG]), match)
        self.assertEqual(tpw.find_matching_wheels(filenames, "Zope.Interface", "7.2.0", [TAG]), match)
        self.assertEqual(tpw.find_matching_wheels(filenames, "zope-interface", "7.2+other", [TAG]), match)
        self.assertEqual(tpw.find_matching_wheels(filenames, "zope-interface", "7.3", [TAG]), [])
        self.assertEqual(tpw.find_matching_wheels(filenames, "zope-component", "7.2", [TAG]), [])
        other_platform = Tag("cp315", "cp315", "manylinux_2_17_aarch64")
        self.assertEqual(tpw.find_matching_wheels(filenames, "zope-interface", "7.2", [other_platform]), [])
        compressed = Tag("cp315", "cp315", "manylinux2014_x86_64")
        self.assertEqual(tpw.find_matching_wheels(filenames, "zope-interface", "7.2", [compressed]), match)


class _Handler(BaseHTTPRequestHandler):
    replies: list[tuple[int, str]] = []
    paths: list[str] = []

    def do_GET(self) -> None:
        _Handler.paths.append(self.path)
        status, body = _Handler.replies.pop(0)
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args: object) -> None:
        pass


class FetchIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.index = f"http://127.0.0.1:{self.server.server_port}/simple"
        _Handler.paths = []
        patcher = mock.patch.object(tpw, "INDEX_RETRY_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The test server is local. Do not send the requests to a proxy.
        environment = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        environment.start()
        self.addCleanup(environment.stop)

    def test_not_found_means_no_files(self) -> None:
        _Handler.replies = [(404, "no packages found")]
        self.assertEqual(tpw.fetch_index_filenames(self.index, "Zope.Interface"), [])
        self.assertEqual(_Handler.paths, ["/simple/zope-interface/"])

    def test_retries_server_errors(self) -> None:
        _Handler.replies = [(503, ""), (500, ""), (200, DEPOT_PAGE)]
        with mock.patch("sys.stderr", io.StringIO()):
            filenames = tpw.fetch_index_filenames(self.index + "/", "zope-interface")
        self.assertEqual(len(filenames), 3)
        self.assertEqual(len(_Handler.paths), 3)

    def test_fails_after_the_last_attempt(self) -> None:
        _Handler.replies = [(503, "")] * tpw.INDEX_ATTEMPTS
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaisesRegex(RuntimeError, "cannot read"):
            tpw.fetch_index_filenames(self.index, "zope-interface")


class RepackTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_repack_adds_the_local_version(self) -> None:
        source = write_wheel(self.root)
        with mock.patch("sys.stdout", io.StringIO()):
            wheel = tpw.repack_wheel(source, "dd.repackaging.42", self.root / "out")
        version = "1.0+dd.repackaging.42"
        dist_info = f"demo_pkg-{version}.dist-info"
        self.assertEqual(wheel.name, f"demo_pkg-{version}-cp315-cp315-manylinux_2_17_x86_64.whl")
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            self.assertIn(f"{dist_info}/METADATA", names)
            self.assertIn(f"demo_pkg-{version}.data/scripts/demo", names)
            self.assertFalse([name for name in names if "demo_pkg-1.0." in name])
            metadata = archive.read(f"{dist_info}/METADATA").decode()
            # Only the header field changes. The description stays the same.
            self.assertEqual(
                metadata,
                f"Metadata-Version: 2.1\nName: demo-pkg\nVersion: {version}\nSummary: Demo\n\n"
                "Description text.\nVersion: 7.7\nFrom a line that email generators change.\n",
            )
            rows = list(csv.reader(io.StringIO(archive.read(f"{dist_info}/RECORD").decode())))
            self.assertEqual({row[0] for row in rows}, names)
            for arcname, digest, size in rows:
                if arcname.endswith("/RECORD"):
                    continue
                data = archive.read(arcname)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                self.assertEqual((digest, int(size)), (f"sha256={expected}", len(data)), arcname)

    def test_repack_rejects_a_wheel_with_a_local_version(self) -> None:
        source = write_wheel(self.root, version="1.0+local")
        with self.assertRaisesRegex(tpw.BuildError, "already has a local version"):
            tpw.repack_wheel(source, "dd.repackaging.42", self.root / "out")

    def test_set_metadata_version_needs_one_matching_header_field(self) -> None:
        metadata = self.root / "METADATA"
        metadata.write_text("Name: demo\nVersion: 2.0\n\nBody\n")
        with self.assertRaisesRegex(tpw.BuildError, "expected one 'Version: 1.0'"):
            tpw.set_metadata_version(metadata, "1.0", "1.0+dd.repackaging.1")


class UploadTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        # A fake adms that records its arguments and exits with $ADMS_EXIT_CODE.
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.calls = self.root / "calls.txt"
        adms = bin_dir / "adms"
        adms.write_text(f'#!/bin/sh\necho "$@" >> "{self.calls}"\nexit "${{ADMS_EXIT_CODE:-0}}"\n')
        adms.chmod(adms.stat().st_mode | stat.S_IXUSR)
        environment = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        environment.start()
        self.addCleanup(environment.stop)

    def add_wheel(self, version: str) -> Path:
        wheel = self.wheels / f"zope_interface-{version}-cp315-cp315-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
        wheel.write_bytes(b"")
        return wheel

    def upload(self, index_filenames: list[str], exit_code: int = 0) -> int:
        with (
            mock.patch.object(tpw, "fetch_index_filenames", return_value=index_filenames),
            mock.patch.dict(os.environ, {"ADMS_EXIT_CODE": str(exit_code)}),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            return tpw.upload(self.wheels, "https://index.example/simple/", "datadog/dd-trace-py-wheels")

    def adms_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_uploads_a_missing_wheel(self) -> None:
        wheel = self.add_wheel("7.2+dd.repackaging.2")
        self.assertEqual(self.upload(["zope.interface-7.2-cp313-cp313-manylinux_2_17_x86_64.whl"]), 0)
        self.assertEqual(
            self.adms_calls(),
            [f"first-party python upload --local --repository datadog/dd-trace-py-wheels {wheel}"],
        )

    def test_skips_a_wheel_that_the_index_has(self) -> None:
        self.add_wheel("7.2+dd.repackaging.2")
        index = ["zope.interface-7.2+dd.repackaging.1-cp315-cp315-manylinux_2_17_x86_64.whl"]
        self.assertEqual(self.upload(index), 0)
        self.assertEqual(self.adms_calls(), [])

    def test_already_exists_is_not_a_failure(self) -> None:
        self.add_wheel("7.2+dd.repackaging.2")
        self.assertEqual(self.upload([], exit_code=tpw.ADMS_ALREADY_EXISTS), 0)

    def test_adms_failure_fails_the_upload(self) -> None:
        self.add_wheel("7.2+dd.repackaging.2")
        self.assertEqual(self.upload([], exit_code=1), 1)

    def test_rejects_a_wheel_without_the_repackaging_local_version(self) -> None:
        self.add_wheel("7.2")
        self.assertEqual(self.upload([]), 1)
        self.assertEqual(self.adms_calls(), [])


if __name__ == "__main__":
    unittest.main()
