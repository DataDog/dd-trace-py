#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "packaging>=24,<27",
#     "wheel>=0.42,<1",
# ]
# ///
"""Build the third-party wheels that PyPI does not publish, and upload them to Depot.

Usage:
  third-party-wheels.py build --platform <platform> --output-dir <dir>
  third-party-wheels.py upload <dir>

.gitlab/third-party-wheels.txt lists the wheels: package, version, Python tag and platform tag.

The build command reads the index of the Depot repository datadog/dd-trace-py-wheels. For each
entry of the platform that the index does not have, it builds the wheel from the sdist in a
manylinux image, repairs it with auditwheel, and adds the local version
"+dd.repackaging.<CI_PIPELINE_ID>". The comparison with the index ignores the local version,
thus a wheel that an earlier pipeline uploaded prevents a new build.

The upload command examines the index again, then uploads each wheel that the index does not
have with adms.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Iterable
from typing import Iterator
import urllib.error
import urllib.parse
import urllib.request

from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename
from packaging.utils import canonicalize_name
from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion
from packaging.version import Version


LIST_FILE = Path(__file__).resolve().parents[1] / "third-party-wheels.txt"
INDEX_URL = "https://depot-read-api-python.us1.ddbuild.io/datadog/dd-trace-py-wheels/@current/simple/"
REPOSITORY = "datadog/dd-trace-py-wheels"
# adms accepts only this local version form for repackaged third-party wheels.
LOCAL_VERSION_PREFIX = "dd.repackaging."
# adms exits with this code when the repository already has the same file.
ADMS_ALREADY_EXISTS = 10
# The manylinux images install each CPython in /opt/python/<python tag>.
MANYLINUX_PYTHONS = Path("/opt/python")
INDEX_ATTEMPTS = 5
INDEX_RETRY_DELAY = 2.0

_PYTHON_TAG_RE = re.compile(r"cp3\d+-cp3\d+t?")
_METADATA_VERSION_RE = re.compile(rb"(?m)^Version:[ \t]*(\S+)[ \t]*\r?$")


class BuildError(Exception):
    """The build of an entry did not give the expected wheel."""


@dataclass(frozen=True)
class Entry:
    """One wheel to build: a package version for one Python tag and one platform tag."""

    name: str
    version: str
    python: str
    platform: str

    @property
    def tag(self) -> Tag:
        interpreter, abi = self.python.split("-")
        return Tag(interpreter, abi, self.platform)

    def __str__(self) -> str:
        return f"{self.name}=={self.version} ({self.tag})"


def read_entries(path: Path) -> list[Entry]:
    """Read the entries of the list file. Raise ValueError if a line is not valid."""
    entries = []
    seen = set()
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        location = f"{path.name}:{number}"
        if len(fields) != 4:
            raise ValueError(f"{location}: expected 4 fields (package version python platform), found {len(fields)}")
        name, version, python, platform = fields
        try:
            parsed = Version(version)
        except InvalidVersion as error:
            raise ValueError(f"{location}: {error}") from error
        if parsed.local is not None:
            raise ValueError(f"{location}: use the upstream version without a local version: {version}")
        if not _PYTHON_TAG_RE.fullmatch(python):
            raise ValueError(f"{location}: expected a CPython tag such as cp313-cp313, found {python}")
        entry = Entry(canonicalize_name(name), version, python, platform)
        key = (entry.name, parsed, entry.tag)
        if key in seen:
            raise ValueError(f"{location}: duplicate entry {entry}")
        seen.add(key)
        entries.append(entry)
    return entries


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.filenames: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag == "a" and href:
            path = urllib.parse.urlsplit(href).path
            self.filenames.append(urllib.parse.unquote(path.rsplit("/", 1)[-1]))


def parse_index_page(html: str) -> list[str]:
    """Return the filenames that a PEP 503 project page links to."""
    parser = _LinkParser()
    parser.feed(html)
    parser.close()
    return parser.filenames


def fetch_index_filenames(index_url: str, name: str) -> list[str]:
    """Return the filenames that the index has for the project name.

    Only a 404 response means that the index has no file. Other failures raise RuntimeError,
    because a failure that looks like a missing wheel would cause a second upload.
    """
    url = urllib.parse.urljoin(index_url.rstrip("/") + "/", canonicalize_name(name) + "/")
    for attempt in range(1, INDEX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return parse_index_page(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 404:
                return []
            failure: Exception = error
        except (OSError, http.client.HTTPException) as error:
            failure = error
        if attempt == INDEX_ATTEMPTS:
            raise RuntimeError(f"cannot read {url} after {attempt} attempts: {failure}") from failure
        delay = INDEX_RETRY_DELAY * attempt
        print(f"Cannot read {url} ({failure}). Try again in {delay:.0f} s.", file=sys.stderr)
        time.sleep(delay)
    raise AssertionError("unreachable")


def find_matching_wheels(filenames: Iterable[str], name: str, version: str, tags: Iterable[Tag]) -> list[str]:
    """Return the wheels for the project name and version that have one of the tags.

    The comparison ignores the local version, because each pipeline uploads a wheel with a
    different local version.
    """
    wanted_name = canonicalize_name(name)
    wanted_version = Version(Version(version).public)
    wanted_tags = frozenset(tags)
    matches = []
    for filename in filenames:
        if not filename.endswith(".whl"):
            continue
        try:
            wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(filename)
        except (InvalidWheelFilename, InvalidVersion):
            print(f"Ignore the file with a name that is not valid: {filename}", file=sys.stderr)
            continue
        if wheel_name == wanted_name and Version(wheel_version.public) == wanted_version and wheel_tags & wanted_tags:
            matches.append(filename)
    return matches


def set_metadata_version(metadata: Path, old_version: str, new_version: str) -> None:
    """Replace the Version header field of a METADATA file."""
    content = metadata.read_bytes()
    # Examine only the header fields. The description after the first blank line can also
    # contain a line that starts with "Version:".
    blank_line = re.search(rb"\r?\n\r?\n", content)
    header_end = blank_line.start() if blank_line else len(content)
    headers, body = content[:header_end], content[header_end:]
    values = _METADATA_VERSION_RE.findall(headers)
    if len(values) != 1 or Version(values[0].decode()) != Version(old_version):
        raise BuildError(f"expected one 'Version: {old_version}' header field in {metadata}, found {values}")
    headers = _METADATA_VERSION_RE.sub(b"Version: " + new_version.encode(), headers, count=1)
    metadata.write_bytes(headers + body)


def repack_wheel(wheel: Path, local_version: str, output_dir: Path) -> Path:
    """Write a copy of the wheel with a local version to output_dir, and return its path.

    The wheel is unpacked and packed again, because the version is also in the .dist-info
    and .data directory names and in METADATA. "wheel pack" makes a new RECORD file.

    "wheel pack" takes the name in the filename from the .dist-info directory. Some build
    backends keep the project name as it is (for example "zope.interface"), but adms accepts
    only the normalized name (for example "zope_interface"). Thus the new directory names
    use the normalized name.
    """
    name, version, _, tags = parse_wheel_filename(wheel.name)
    if version.local is not None:
        raise BuildError(f"{wheel.name} already has a local version")
    new_version = f"{version}+{local_version}"
    new_name = name.replace("-", "_")
    with tempfile.TemporaryDirectory() as temporary:
        unpack_dir = Path(temporary, "unpacked")
        pack_dir = Path(temporary, "packed")
        _run([sys.executable, "-m", "wheel", "unpack", "--dest", str(unpack_dir), str(wheel)])
        (root,) = unpack_dir.iterdir()
        dist_infos = list(root.glob("*.dist-info"))
        if len(dist_infos) != 1:
            raise BuildError(f"expected one .dist-info directory in {wheel.name}, found {len(dist_infos)}")
        dist_info = dist_infos[0]
        dist_name, _, old_version = dist_info.name[: -len(".dist-info")].rpartition("-")
        set_metadata_version(dist_info / "METADATA", old_version, new_version)
        data = root / f"{dist_name}-{old_version}.data"
        if data.is_dir():
            data.rename(root / f"{new_name}-{new_version}.data")
        dist_info.rename(root / f"{new_name}-{new_version}.dist-info")
        pack_dir.mkdir()
        _run([sys.executable, "-m", "wheel", "pack", "--dest-dir", str(pack_dir), str(root)])
        (packed,) = pack_dir.glob("*.whl")
        packed_name, packed_version, _, packed_tags = parse_wheel_filename(packed.name)
        if (packed_name, str(packed_version), packed_tags) != (name, new_version, tags):
            raise BuildError(f"unexpected name, version or tags in the repacked wheel {packed.name}")
        output_dir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(packed), str(output_dir / packed.name)))


def _run(command: list[str]) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def _single_wheel(directory: Path) -> Path:
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"expected one wheel in {directory}, found {[wheel.name for wheel in wheels]}")
    return wheels[0]


@contextlib.contextmanager
def _section(name: str, header: str) -> Iterator[None]:
    # Collapsible section in the GitLab job log.
    print(f"\x1b[0Ksection_start:{int(time.time())}:{name}[collapsed=true]\r\x1b[0K{header}", flush=True)
    try:
        yield
    finally:
        print(f"\x1b[0Ksection_end:{int(time.time())}:{name}\r\x1b[0K", flush=True)


def _check_install(python: Path, wheel: Path, name: str, version: str, work_dir: Path) -> None:
    """Install the wheel in a new virtual environment, and make sure that it has the version."""
    venv = work_dir / "venv"
    _run([str(python), "-m", "venv", str(venv)])
    venv_python = venv / "bin" / "python"
    _run(
        [str(venv_python), "-m", "pip", "install", "--no-deps", "--no-index", "--disable-pip-version-check", str(wheel)]
    )
    installed = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import importlib.metadata, sys; print(importlib.metadata.version(sys.argv[1]))",
            name,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if installed != version:
        raise BuildError(f"the installed {name} has the version {installed}, expected {version}")


def build_entry(entry: Entry, local_version: str, output_dir: Path, work_dir: Path) -> Path:
    """Build, repair, repack and install the wheel of the entry. Return the path of the wheel."""
    python = MANYLINUX_PYTHONS / entry.python / "bin" / "python"
    if shutil.which("manylinux-interpreters"):
        _run(["manylinux-interpreters", "ensure", entry.python])
    built_dir = work_dir / "built"
    repaired_dir = work_dir / "repaired"
    _run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-cache-dir",
            "--disable-pip-version-check",
            # Build from the sdist, also if the index has a wheel.
            "--no-binary",
            entry.name,
            "--wheel-dir",
            str(built_dir),
            f"{entry.name}=={entry.version}",
        ]
    )
    _run(
        [
            "auditwheel",
            "repair",
            "--plat",
            entry.platform,
            "--wheel-dir",
            str(repaired_dir),
            str(_single_wheel(built_dir)),
        ]
    )
    repaired = _single_wheel(repaired_dir)
    name, version, _, tags = parse_wheel_filename(repaired.name)
    if name != entry.name or version != Version(entry.version):
        raise BuildError(f"expected a wheel for {entry.name}=={entry.version}, found {repaired.name}")
    if entry.tag not in tags:
        raise BuildError(f"{repaired.name} does not have the tag {entry.tag}")
    wheel = repack_wheel(repaired, local_version, output_dir)
    _check_install(python, wheel, entry.name, str(parse_wheel_filename(wheel.name)[1]), work_dir)
    return wheel


def build(entries: list[Entry], platform: str, index_url: str, local_version: str, output_dir: Path) -> int:
    selected = [entry for entry in entries if entry.platform == platform]
    print(f"{len(selected)} of {len(entries)} entries are for {platform}.")
    output_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, list[str]] = {}
    built, failed = [], []
    for number, entry in enumerate(selected):
        if entry.name not in index:
            index[entry.name] = fetch_index_filenames(index_url, entry.name)
        matches = find_matching_wheels(index[entry.name], entry.name, entry.version, [entry.tag])
        if matches:
            print(f"Skip {entry}: the index has {matches[0]}")
            continue
        with _section(f"build_{number}", f"Build {entry}"), tempfile.TemporaryDirectory() as work_dir:
            try:
                wheel = build_entry(entry, local_version, output_dir, Path(work_dir))
            except (BuildError, OSError, ValueError, subprocess.CalledProcessError) as error:
                print(f"ERROR: cannot build {entry}: {error}", file=sys.stderr)
                failed.append(entry)
                continue
        print(f"Built {wheel.name}")
        built.append(wheel)
    print(f"Built {len(built)} wheels in {output_dir}.")
    if failed:
        print("ERROR: cannot build these entries:", file=sys.stderr)
        for entry in failed:
            print(f"  {entry}", file=sys.stderr)
        return 1
    return 0


def upload(directory: Path, index_url: str, repository: str) -> int:
    wheels = sorted(directory.glob("*.whl"))
    if not wheels:
        print(f"No wheels to upload in {directory}.")
        return 0
    failed = []
    for wheel in wheels:
        name, version, _, tags = parse_wheel_filename(wheel.name)
        if not (version.local or "").startswith(LOCAL_VERSION_PREFIX):
            print(f"ERROR: {wheel.name} does not have a local version {LOCAL_VERSION_PREFIX}<id>", file=sys.stderr)
            failed.append(wheel)
            continue
        # Another pipeline can upload the same wheel while this pipeline builds it.
        matches = find_matching_wheels(fetch_index_filenames(index_url, name), name, version.public, tags)
        if matches:
            print(f"Skip {wheel.name}: the index has {matches[0]}")
            continue
        # adms 0.0.29 and later know this repository. Older versions also need --local.
        command = ["adms", "first-party", "python", "upload", "--local", "--repository", repository, str(wheel)]
        print("+ " + " ".join(command), flush=True)
        result = subprocess.run(command)
        if result.returncode == ADMS_ALREADY_EXISTS:
            print(f"{repository} already has {wheel.name}")
        elif result.returncode != 0:
            print(f"ERROR: cannot upload {wheel.name} (adms exit code {result.returncode})", file=sys.stderr)
            failed.append(wheel)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-url", default=INDEX_URL, help="PEP 503 index to examine (default: %(default)s)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build the wheels that the index does not have.")
    build_parser.add_argument("--platform", required=True, help="platform tag, e.g. manylinux_2_17_x86_64")
    build_parser.add_argument("--output-dir", required=True, type=Path, help="directory for the built wheels")
    build_parser.add_argument("--list", type=Path, default=LIST_FILE, help="list file (default: %(default)s)")
    build_parser.add_argument(
        "--pipeline-id",
        default=os.environ.get("CI_PIPELINE_ID", ""),
        help="ID for the local version (default: $CI_PIPELINE_ID)",
    )

    upload_parser = subparsers.add_parser("upload", help="Upload the wheels that the index does not have.")
    upload_parser.add_argument("directory", type=Path, help="directory with the wheels to upload")
    upload_parser.add_argument("--repository", default=REPOSITORY, help="Depot repository (default: %(default)s)")

    args = parser.parse_args(argv)
    if args.command == "build":
        if not args.pipeline_id.isdigit():
            parser.error(f"--pipeline-id must be a number, found {args.pipeline_id!r}")
        try:
            entries = read_entries(args.list)
        except ValueError as error:
            parser.error(str(error))
        return build(entries, args.platform, args.index_url, LOCAL_VERSION_PREFIX + args.pipeline_id, args.output_dir)
    return upload(args.directory, args.index_url, args.repository)


if __name__ == "__main__":
    sys.exit(main())
