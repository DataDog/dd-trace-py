"""Wheel and sdist helpers shared by the artifact-oriented gates.

Wheel inspection is pure stdlib (zipfile + email parsing + packaging for
names/versions), so gates can analyze artifacts without installing them.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
import csv
from dataclasses import dataclass
import email.parser
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404
import sys
import tarfile
from typing import Optional
import zipfile

from packaging.utils import parse_wheel_filename


# File suffixes that indicate native (compiled) content.
NATIVE_SUFFIXES = (".so", ".pyd", ".dylib", ".o", ".a", ".obj", ".lib")


def is_native_file(path: str) -> bool:
    return path.endswith(NATIVE_SUFFIXES)


class BuildError(Exception):
    pass


@dataclass
class WheelInfo:
    path: Path
    name: str
    version: str
    files: list[str]
    metadata: dict[str, list[str]]  # lower-cased METADATA headers -> values
    entry_points: dict[str, dict[str, str]]  # group -> name -> value

    @property
    def dist_info(self) -> str:
        return "%s-%s.dist-info" % (
            self.name.replace("-", "_"),
            self.version.replace("-", "_"),
        )

    def package_files(self) -> list[str]:
        """Files owned by the distribution, excluding metadata directories."""
        marker = self.dist_info + "/"
        return [f for f in self.files if not f.startswith(marker)]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_entry_points(text: str) -> dict[str, dict[str, str]]:
    """Parse an entry_points.txt (INI format) into {group: {name: value}}."""
    import configparser

    class EntryPointParser(configparser.ConfigParser):
        def optionxform(self, optionstr: str) -> str:
            return optionstr

    parser = EntryPointParser()
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise BuildError("invalid entry_points.txt: %s" % exc) from exc
    return {section: {name: value for name, value in parser.items(section)} for section in parser.sections()}


def read_wheel(path: Path) -> WheelInfo:
    name, version, _, _ = parse_wheel_filename(path.name)
    version_text = str(version)
    name_text = str(name)
    with zipfile.ZipFile(path) as zf:
        files = zf.namelist()
        dist_info = "%s-%s.dist-info" % (name_text.replace("-", "_"), version_text.replace("-", "_"))
        metadata_bytes = zf.read(dist_info + "/METADATA")
        metadata = email.parser.BytesParser().parsebytes(metadata_bytes)
        headers: dict[str, list[str]] = {}
        for key, value in metadata.items():
            headers.setdefault(key.lower(), []).append(value)
        entry_points: dict[str, dict[str, str]] = {}
        ep_name = dist_info + "/entry_points.txt"
        if ep_name in files:
            entry_points = _parse_entry_points(zf.read(ep_name).decode("utf-8"))
    return WheelInfo(
        path=path,
        name=name_text,
        version=version_text,
        files=sorted(files),
        metadata=headers,
        entry_points=entry_points,
    )


def unpack_wheel(path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        for member in zf.namelist():
            # Defensive member sanitization (no absolute paths or ..).
            target = (destination / member).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise BuildError("wheel %s contains unsafe path %r" % (path.name, member))
        zf.extractall(destination)  # nosec B202 - members validated above
    return destination


def unpack_sdist(path: Path, destination: Path) -> Path:
    """Extract a source distribution; returns the directory that contains setup.py/pyproject."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path) as tf:
        members = tf.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise BuildError("sdist %s contains unsafe path %r" % (path.name, member.name))
            if not (member.isfile() or member.isdir()):
                raise BuildError("sdist %s contains link or special file %r" % (path.name, member.name))
        tf.extractall(destination)  # nosec B202 - paths and member types validated above
    roots = sorted({Path(m.name).parts[0] for m in members if Path(m.name).parts})
    if len(roots) != 1:
        raise BuildError("sdist %s must contain exactly one root directory" % path.name)
    root_dir = destination / roots[0]
    if not (root_dir / "pyproject.toml").is_file() and not (root_dir / "setup.py").is_file():
        raise BuildError("sdist %s has no build metadata at its root" % path.name)
    return root_dir


def build_distributions(
    source_dir: Path,
    output_dir: Path,
    build_command: Optional[list[str]] = None,
) -> dict[str, Path]:
    """Build sdist + wheel into output_dir; return {kind: path}.

    Uses the manifest's build_command when declared (it must place artifacts
    in the directory given by the OUTPUT_DIR environment variable), otherwise
    ``uv build`` when available, otherwise ``python -m build``. Both
    fallbacks use PEP 517 build isolation.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if build_command:
        command = list(build_command)
        env = dict(os.environ)
        env["OUTPUT_DIR"] = str(output_dir)
        result = subprocess.run(  # nosec B603
            command, cwd=str(source_dir), env=env, capture_output=True, text=True, timeout=3600
        )
        if result.returncode != 0:
            raise BuildError("build command failed (%d): %s" % (result.returncode, result.stderr[-4000:]))
    else:
        runner = _default_build_runner()
        runner(source_dir, output_dir)
    artifacts = _collect_artifacts(output_dir)
    missing = {"sdist", "wheel"} - set(artifacts)
    if missing:
        raise BuildError("build did not produce: %s" % ", ".join(sorted(missing)))
    return artifacts


def _default_build_runner() -> Callable[[Path, Path], None]:
    """Pick a working build runner.

    Prefers uv (the repository standard), validating each candidate by
    executing ``--version`` first: absolute paths matter because PATH can
    contain broken shims (e.g. pyenv shims resolving to a Python version
    that lacks uv). Falls back to ``python -m build``.
    """
    candidates: list[str] = []
    uv = shutil.which("uv")
    if uv:
        candidates.append(uv)

    def runner(source_dir: Path, output_dir: Path) -> None:
        errors: list[str] = []
        for candidate in candidates:
            # Validate in the build's working directory: shim-based uv
            # installs (pyenv) resolve per-cwd, so a candidate that works
            # from the repository root may not work from the package dir.
            check = subprocess.run(  # nosec B603
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(source_dir),
            )
            if check.returncode == 0:
                return _uv_build(candidate, source_dir, output_dir)
            errors.append("uv candidate %r unusable: %s" % (candidate, check.stderr.strip()[:200]))
        try:
            return _python_build(source_dir, output_dir)
        except BuildError as exc:
            raise BuildError("; ".join(errors + [str(exc)])) from exc

    return runner


def _uv_build(uv: str, source_dir: Path, output_dir: Path) -> None:
    result = subprocess.run(  # nosec B603
        [uv, "build", "--sdist", "--wheel", "--out-dir", str(output_dir)],
        cwd=str(source_dir),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.returncode != 0:
        raise BuildError("uv build failed: %s" % (result.stderr[-4000:] or result.stdout[-4000:]))


def _python_build(source_dir: Path, output_dir: Path) -> None:
    result = subprocess.run(  # nosec B603
        [sys.executable, "-m", "build", "--outdir", str(output_dir), str(source_dir)],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.returncode != 0:
        raise BuildError("python -m build failed: %s" % (result.stderr[-4000:] or result.stdout[-4000:]))


def _collect_artifacts(output_dir: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for path in sorted(output_dir.iterdir()):
        # NOTE: Path.suffix on "x.tar.gz" is ".gz"; match on the name.
        if path.name.endswith(".whl"):
            kind = "wheel"
        elif path.name.endswith((".tar.gz", ".tgz")):
            kind = "sdist"
        else:
            continue
        if kind in artifacts:
            raise BuildError("multiple %s artifacts in %s" % (kind, output_dir))
        artifacts[kind] = path
    return artifacts


def wheel_native_files(wheel: WheelInfo) -> list[str]:
    return [f for f in wheel.files if is_native_file(f)]


def venv_native_files(venv_dir: Path, installed: dict[str, str]) -> list[str]:
    """Scan every installed distribution's RECORD for native content."""
    return [
        "%s:%s" % (distribution, path)
        for distribution, files in distribution_record_files(venv_dir, installed).items()
        for path in files
        if is_native_file(path)
    ]


def _site_packages(venv_dir: Path) -> Path:
    # venv layout: <venv>/lib/pythonX.Y/site-packages (posix) or
    # Lib/site-packages (Windows).
    if os.name == "nt":
        return venv_dir / "Lib" / "site-packages"
    return venv_dir / "lib" / ("python%s.%s" % sys.version_info[:2]) / "site-packages"


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def distribution_record_files(venv_dir: Path, installed: dict[str, str]) -> dict[str, list[str]]:
    """Map installed distribution -> list of files it owns (from RECORD).

    Missing RECORDs fail closed: ownership and native content cannot be verified.
    """
    site_packages = _site_packages(venv_dir)
    owned: dict[str, list[str]] = {}
    records = {}
    for dist_info in site_packages.glob("*.dist-info"):
        metadata = email.parser.Parser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8"))
        records[_normalized_name(metadata["Name"])] = dist_info / "RECORD"
    for distribution in sorted(installed):
        record = records.get(_normalized_name(distribution))
        if record is None or not record.is_file():
            raise BuildError("cannot verify %s: missing RECORD" % distribution)
        with record.open(encoding="utf-8", newline="") as handle:
            owned[distribution] = [os.path.normpath(row[0]) for row in csv.reader(handle) if row and row[0]]
    return owned


def entry_point_counts(venv_dir: Path, groups: Iterable[str]) -> dict[str, list[str]]:
    """Entry points per group, with owning distribution, from disk metadata."""
    site_packages = _site_packages(venv_dir)
    counts: dict[str, list[str]] = {group: [] for group in groups}
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        entry_points_file = dist_info / "entry_points.txt"
        if not entry_points_file.is_file():
            continue
        for group, entries in _parse_entry_points(entry_points_file.read_text(encoding="utf-8")).items():
            if group in counts:
                counts[group].extend("%s (from %s)" % (name, dist_info.name.split(".dist-info")[0]) for name in entries)
    return counts
