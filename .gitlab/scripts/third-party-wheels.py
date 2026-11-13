#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "auditwheel>=6.7,<7",
#     "packaging>=24,<27",
#     "patchelf>=0.17,<1",
#     "ruamel.yaml>=0.17.21",
#     "wheel>=0.42,<1",
# ]
# ///
"""Build the wheels that PyPI does not publish for the test locks, and upload them to Depot.

Usage:
  third-party-wheels.py build --python <version> --output-dir <dir>
  third-party-wheels.py upload <dir>

The build command reads the pins (name==version) of the test locks in tests/requirements_locks
for one Python version. tests/suitespec.py gives the Python version of each lock. A pin needs
a wheel when the PyPI mirror has its sdist, but no wheel that the test image can install. The
test image is x86_64 Linux with glibc 2.41.

The command then reads the index of the Depot repository datadog/dd-trace-py-wheels. The
comparison with the index ignores the local version, because each pipeline gives a different
local version to its wheels. Thus a wheel that an earlier pipeline uploaded prevents a new
build. For each pin that the index does not have, the command builds the wheel from the sdist
in the test image, because that image has the system libraries that the test jobs use to build
the packages. auditwheel copies the system libraries into a platform wheel and gives it the
most compatible manylinux tag. When auditwheel cannot do this, the wheel keeps its
linux_x86_64 tag, because the test jobs run in the same image. A pure-Python wheel gets the tag
of the CPython version (for example cp313-none-any), because the metadata of a wheel can
depend on the Python version that built it. Then the command adds the local version
"+dd.repackaging.<CI_PIPELINE_ID>".

The upload command examines the index again, then uploads each wheel that the index does not
have with adms.

.gitlab/third-party-wheels-exclude.txt lists the packages that the command does not build.
"""

from __future__ import annotations

import argparse
import concurrent.futures
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
import urllib.error
import urllib.parse
import urllib.request

from packaging.tags import Tag
from packaging.tags import compatible_tags
from packaging.tags import cpython_tags
from packaging.utils import InvalidSdistFilename
from packaging.utils import InvalidWheelFilename
from packaging.utils import canonicalize_name
from packaging.utils import parse_sdist_filename
from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion
from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.suitespec import get_test_environments  # noqa: E402


EXCLUDE_FILE = REPO_ROOT / ".gitlab" / "third-party-wheels-exclude.txt"
UPSTREAM_INDEX_URL = "https://depot-read-api-python.us1.ddbuild.io/datadog/python/@current/simple/"
INDEX_URL = "https://depot-read-api-python.us1.ddbuild.io/datadog/dd-trace-py-wheels/@current/simple/"
REPOSITORY = "datadog/dd-trace-py-wheels"
# adms accepts only this local version form for repackaged third-party wheels.
LOCAL_VERSION_PREFIX = "dd.repackaging."
# adms exits with this code when the repository already has the same file.
ADMS_ALREADY_EXISTS = 10
# The build command exits with this code when it cannot build some of the wheels.
EXIT_BUILD_FAILURES = 3
# The test image (TESTRUNNER_IMAGE) is Debian 13 on x86_64, with glibc 2.41.
TEST_IMAGE_GLIBC_MINOR = 41
# auditwheel exits with this message when the wheel has no compiled code.
AUDITWHEEL_NOT_PLATFORM_WHEEL = "This does not look like a platform wheel"
INDEX_ATTEMPTS = 5
INDEX_RETRY_DELAY = 2.0
INDEX_THREADS = 16
BUILD_TIMEOUT = 30 * 60

_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;]+)$")
_METADATA_VERSION_RE = re.compile(rb"(?m)^Version:[ \t]*(\S+)[ \t]*\r?$")
_LEGACY_MANYLINUX = {17: "manylinux2014", 12: "manylinux2010", 5: "manylinux1"}


class BuildError(Exception):
    """The build of a pin did not give the expected wheel."""


@dataclass(frozen=True)
class Pin:
    """A name==version pin of a test lock."""

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}=={self.version}"


def read_pins(python: str) -> list[Pin]:
    """Return the pins of all the test locks for the Python version, for example "3.13"."""
    pins = set()
    for environments in get_test_environments(nightly=False).values():
        for environment in environments:
            lockfile = REPO_ROOT / environment.lockfile
            if environment.python != python or not lockfile.is_file():
                continue
            for line in lockfile.read_text().splitlines():
                match = _PIN_RE.match(line.strip())
                if not match:
                    continue
                try:
                    version = Version(match[2])
                except InvalidVersion:
                    continue
                # A pin with a local version cannot get the repackaging local version.
                if version.local is None:
                    pins.add(Pin(canonicalize_name(match[1]), match[2]))
    return sorted(pins, key=lambda pin: (pin.name, Version(pin.version)))


def read_exclusions(path: Path) -> set[tuple[str, str | None]]:
    """Read the exclusion file. Each line is "name" (all versions) or "name==version"."""
    exclusions: set[tuple[str, str | None]] = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, version = line.partition("==")
            exclusions.add((canonicalize_name(name.strip()), version.strip() or None))
    return exclusions


def is_excluded(pin: Pin, exclusions: set[tuple[str, str | None]]) -> bool:
    return (pin.name, None) in exclusions or (pin.name, pin.version) in exclusions


def python_tags(python: str) -> tuple[str, frozenset[Tag]]:
    """Return the CPython tag (for example "cp313") and the wheel tags that the test image accepts."""
    major, minor = (int(part) for part in python.split("."))
    interpreter = f"cp{major}{minor}"
    platforms = []
    for glibc_minor in range(TEST_IMAGE_GLIBC_MINOR, 4, -1):
        platforms.append(f"manylinux_2_{glibc_minor}_x86_64")
        if glibc_minor in _LEGACY_MANYLINUX:
            platforms.append(f"{_LEGACY_MANYLINUX[glibc_minor]}_x86_64")
    platforms.append("linux_x86_64")
    tags = set(cpython_tags((major, minor), [interpreter], platforms))
    tags.update(compatible_tags((major, minor), interpreter, platforms))
    return interpreter, frozenset(tags)


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


def fetch_many(index_url: str, names: Iterable[str]) -> dict[str, list[str]]:
    """Return the filenames that the index has for each project name."""
    names = sorted(set(names))
    with concurrent.futures.ThreadPoolExecutor(INDEX_THREADS) as executor:
        return dict(zip(names, executor.map(lambda name: fetch_index_filenames(index_url, name), names)))


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
            continue
        if wheel_name == wanted_name and Version(wheel_version.public) == wanted_version and wheel_tags & wanted_tags:
            matches.append(filename)
    return matches


def has_sdist(filenames: Iterable[str], name: str, version: str) -> bool:
    for filename in filenames:
        if filename.endswith(".whl"):
            continue
        try:
            sdist_name, sdist_version = parse_sdist_filename(filename)
        except (InvalidSdistFilename, InvalidVersion):
            continue
        if sdist_name == canonicalize_name(name) and sdist_version == Version(version):
            return True
    return False


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


def set_wheel_tag(wheel_file: Path, tag: str) -> None:
    """Replace the Tag fields of a WHEEL file with one tag."""
    lines = [line for line in wheel_file.read_text().splitlines() if line.strip() and not line.startswith("Tag:")]
    wheel_file.write_text("\n".join([*lines, f"Tag: {tag}"]) + "\n")


def repack_wheel(wheel: Path, local_version: str, output_dir: Path, log: list[str], tag: str | None = None) -> Path:
    """Write a copy of the wheel with a local version to output_dir, and return its path.

    The wheel is unpacked and packed again, because the version is also in the .dist-info
    and .data directory names and in METADATA. "wheel pack" makes a new RECORD file. If tag
    is set, it replaces the tags of the wheel.

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
    new_tags = frozenset([Tag(*tag.split("-"))]) if tag else tags
    with tempfile.TemporaryDirectory() as temporary:
        unpack_dir = Path(temporary, "unpacked")
        pack_dir = Path(temporary, "packed")
        _run([sys.executable, "-m", "wheel", "unpack", "--dest", str(unpack_dir), str(wheel)], log)
        (root,) = unpack_dir.iterdir()
        dist_infos = list(root.glob("*.dist-info"))
        if len(dist_infos) != 1:
            raise BuildError(f"expected one .dist-info directory in {wheel.name}, found {len(dist_infos)}")
        dist_info = dist_infos[0]
        dist_name, _, old_version = dist_info.name[: -len(".dist-info")].rpartition("-")
        set_metadata_version(dist_info / "METADATA", old_version, new_version)
        if tag:
            set_wheel_tag(dist_info / "WHEEL", tag)
        data = root / f"{dist_name}-{old_version}.data"
        if data.is_dir():
            data.rename(root / f"{new_name}-{new_version}.data")
        dist_info.rename(root / f"{new_name}-{new_version}.dist-info")
        pack_dir.mkdir()
        _run([sys.executable, "-m", "wheel", "pack", "--dest-dir", str(pack_dir), str(root)], log)
        (packed,) = pack_dir.glob("*.whl")
        packed_name, packed_version, _, packed_tags = parse_wheel_filename(packed.name)
        if (packed_name, str(packed_version), packed_tags) != (name, new_version, new_tags):
            raise BuildError(f"unexpected name, version or tags in the repacked wheel {packed.name}")
        output_dir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(packed), str(output_dir / packed.name)))


def _run(command: list[str], log: list[str], timeout: float | None = None, env: dict[str, str] | None = None) -> str:
    """Run the command, add the command and its output to the log, and return the output."""
    log.append("+ " + " ".join(command))
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False, env=env
        )
    except subprocess.TimeoutExpired as error:
        raise BuildError(f"{Path(command[0]).name} did not stop in {timeout:.0f} s") from error
    log.append(result.stdout)
    if result.returncode != 0:
        raise BuildError(f"{' '.join(command[:4])} ... exited with code {result.returncode}")
    return result.stdout


def _single_wheel(directory: Path) -> Path:
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"expected one wheel in {directory}, found {[wheel.name for wheel in wheels]}")
    return wheels[0]


def _print_section(name: str, header: str, lines: list[str]) -> None:
    # Collapsible section in the GitLab job log.
    print(f"\x1b[0Ksection_start:{int(time.time())}:{name}[collapsed=true]\r\x1b[0K{header}")
    print("\n".join(line.rstrip("\n") for line in lines))
    print(f"\x1b[0Ksection_end:{int(time.time())}:{name}\r\x1b[0K", flush=True)


def repair_wheel(wheel: Path, output_dir: Path, log: list[str]) -> Path:
    """Repair a platform wheel with auditwheel, and return the path of the wheel to use.

    Return the wheel as it is when auditwheel cannot give it a manylinux tag.
    """
    # auditwheel runs the patchelf of the environment of this script.
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    try:
        _run([sys.executable, "-m", "auditwheel", "repair", "--wheel-dir", str(output_dir), str(wheel)], log, env=env)
    except BuildError:
        if AUDITWHEEL_NOT_PLATFORM_WHEEL in log[-1]:
            raise BuildError("the platform wheel has no compiled code") from None
        log.append(f"auditwheel cannot give a manylinux tag to {wheel.name}. Keep the wheel as it is.")
        return wheel
    return _single_wheel(output_dir)


def find_python(python: str) -> str:
    """Return the path of the python of the version on PATH.

    "uv run" puts the environment of this script first on PATH. That environment has no pip,
    thus it is not used, also when it has the same Python version.
    """
    own_bin = Path(sys.executable).parent.resolve()
    path = os.pathsep.join(
        d for d in os.environ.get("PATH", "").split(os.pathsep) if d and Path(d).resolve() != own_bin
    )
    python_bin = shutil.which(f"python{python}", path=path)
    if not python_bin:
        raise RuntimeError(f"cannot find python{python} on PATH")
    return python_bin


def build_pin(pin: Pin, python: str, python_bin: str, local_version: str, output_dir: Path, log: list[str]) -> Path:
    """Build, repair, repack and install the wheel of the pin. Return the path of the wheel."""
    interpreter, tags = python_tags(python)
    with tempfile.TemporaryDirectory() as temporary:
        work_dir = Path(temporary)
        built_dir = work_dir / "built"
        repaired_dir = work_dir / "repaired"
        _run(
            [
                python_bin,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-cache-dir",
                "--disable-pip-version-check",
                # Build from the sdist, also if the index has a wheel.
                "--no-binary",
                pin.name,
                "--wheel-dir",
                str(built_dir),
                str(pin),
            ],
            log,
            timeout=BUILD_TIMEOUT,
        )
        built = _single_wheel(built_dir)
        if all(tag.abi == "none" and tag.platform == "any" for tag in parse_wheel_filename(built.name)[3]):
            wheel = repack_wheel(built, local_version, output_dir, log, tag=f"{interpreter}-none-any")
        else:
            wheel = repack_wheel(repair_wheel(built, repaired_dir, log), local_version, output_dir, log)
        name, version, _, wheel_tags = parse_wheel_filename(wheel.name)
        if name != pin.name or Version(version.public) != Version(pin.version):
            raise BuildError(f"expected a wheel for {pin}, found {wheel.name}")
        if not wheel_tags & tags:
            raise BuildError(f"Python {python} on the test image cannot install {wheel.name}")
        # Make sure that the wheel installs, and that the installed version is the new version.
        venv = work_dir / "venv"
        _run([python_bin, "-m", "venv", str(venv)], log)
        pip = [str(venv / "bin" / "python"), "-m", "pip", "--disable-pip-version-check"]
        _run([*pip, "install", "--no-deps", "--no-index", str(wheel)], log)
        installed = re.search(r"(?m)^Version: (\S+)$", _run([*pip, "show", pin.name], log))
        if not installed or installed[1] != str(version):
            raise BuildError(f"the installed {pin.name} does not have the version {version}")
        return wheel


def build(python: str, local_version: str, output_dir: Path, jobs: int, upstream_url: str, index_url: str) -> int:
    _, tags = python_tags(python)
    exclusions = read_exclusions(EXCLUDE_FILE)
    pins = read_pins(python)
    upstream = fetch_many(upstream_url, (pin.name for pin in pins))
    missing = [
        pin
        for pin in pins
        if has_sdist(upstream[pin.name], pin.name, pin.version)
        and not find_matching_wheels(upstream[pin.name], pin.name, pin.version, tags)
    ]
    print(f"Python {python}: {len(pins)} pins, {len(missing)} with no wheel on {upstream_url}")
    index = fetch_many(index_url, (pin.name for pin in missing))
    to_build = []
    for pin in missing:
        matches = find_matching_wheels(index[pin.name], pin.name, pin.version, tags)
        if matches:
            print(f"  {pin}: the index has {matches[0]}")
        elif is_excluded(pin, exclusions):
            print(f"  {pin}: excluded by {EXCLUDE_FILE.name}")
        else:
            print(f"  {pin}: build")
            to_build.append(pin)
    if not to_build:
        print("Nothing to build.")
        return 0

    python_bin = find_python(python)
    output_dir.mkdir(parents=True, exist_ok=True)
    built, failed = [], []
    with concurrent.futures.ThreadPoolExecutor(jobs) as executor:
        logs: dict[Pin, list[str]] = {pin: [] for pin in to_build}
        futures = {
            executor.submit(build_pin, pin, python, python_bin, local_version, output_dir, logs[pin]): pin
            for pin in to_build
        }
        for number, future in enumerate(concurrent.futures.as_completed(futures)):
            pin = futures[future]
            try:
                wheel = future.result()
            except (BuildError, OSError, ValueError) as error:
                failed.append((pin, error))
                _print_section(f"build_{number}", f"FAILED: {pin}: {error}", logs[pin])
            else:
                built.append(wheel)
                _print_section(f"build_{number}", f"Built {wheel.name}", logs[pin])
    print(f"Built {len(built)} wheels in {output_dir}.")
    if failed:
        print(f"ERROR: cannot build {len(failed)} wheels:", file=sys.stderr)
        for pin, error in sorted(failed, key=lambda item: item[0].name):
            print(f"  {pin}: {error}", file=sys.stderr)
        print(f"To stop the builds of a package, add it to {EXCLUDE_FILE.name}.", file=sys.stderr)
        return EXIT_BUILD_FAILURES
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

    build_parser = subparsers.add_parser("build", help="Build the wheels that PyPI and the index do not have.")
    build_parser.add_argument("--python", required=True, help="Python version of the test locks, e.g. 3.13")
    build_parser.add_argument("--output-dir", required=True, type=Path, help="directory for the built wheels")
    build_parser.add_argument("--jobs", type=int, default=4, help="number of builds at the same time")
    build_parser.add_argument(
        "--upstream-index-url", default=UPSTREAM_INDEX_URL, help="PyPI mirror (default: %(default)s)"
    )
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
        if not re.fullmatch(r"3\.\d+", args.python):
            parser.error(f"--python must be a CPython 3 version such as 3.13, found {args.python!r}")
        return build(
            args.python,
            LOCAL_VERSION_PREFIX + args.pipeline_id,
            args.output_dir,
            args.jobs,
            args.upstream_index_url,
            args.index_url,
        )
    return upload(args.directory, args.index_url, args.repository)


if __name__ == "__main__":
    sys.exit(main())
