from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Iterator


# Two days matches the cross-language supply-chain cooldown used by test locks.
COOLDOWN_DAYS = 2
_CACHE_ROOT = Path(".cache/uv-test-environments")
_PACKAGE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_PIN = re.compile(rf"^\s*(?P<name>{_PACKAGE_NAME})(?:\[[^]]+\])?\s*==\s*(?P<version>[^\s;#]+)")
_DIRECT = re.compile(rf"^\s*(?P<name>{_PACKAGE_NAME})(?:\[[^]]+\])?\s*@\s*\S+")
_NORMALIZE_NAME = re.compile(r"[-_.]+")
_STATE_FILE = ".ddtrace-test-environment.json"
_STATE_VERSION = 1


class UvTestEnvironmentError(RuntimeError):
    """Raised when a test environment cannot be prepared or validated."""


@dataclass(frozen=True)
class PreparedEnvironment:
    path: Path
    requirements: Path
    package_hash: str
    packages: tuple[tuple[str, str], ...]
    direct_packages: tuple[str, ...]
    install_project: bool
    project_hash: str | None


def normalize_package_name(name: str) -> str:
    return _NORMALIZE_NAME.sub("-", name).lower()


def locked_packages(contents: str) -> tuple[tuple[str, str], ...]:
    """Return normalized exact pins from requirements-style lock contents."""
    packages = {
        (normalize_package_name(match.group("name")), match.group("version"))
        for line in contents.splitlines()
        if (match := _PIN.match(line)) is not None
    }
    return tuple(sorted(packages))


def package_content_hash(contents: str) -> str:
    """Return a stable short hash of normalized package names and versions."""
    normalized = "\n".join(f"{name}=={version}" for name, version in locked_packages(contents))
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def cooldown_cutoff(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise UvTestEnvironmentError("cooldown timestamp must be timezone-aware")
    cutoff = current.astimezone(dt.timezone.utc) - dt.timedelta(days=COOLDOWN_DAYS)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _direct_packages(contents: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                normalize_package_name(match.group("name"))
                for line in contents.splitlines()
                if (match := _DIRECT.match(line)) is not None
            }
        )
    )


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary.write(contents)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _editable_requirements(root: Path, lockfile: Path, contents: str, package_hash: str) -> Path:
    relative = _CACHE_ROOT / ".requirements" / f"{lockfile.stem}-{package_hash}.txt"
    path = root / relative
    filtered = "".join(
        line
        for line in contents.splitlines(keepends=True)
        if not ((match := _PIN.match(line)) and normalize_package_name(match.group("name")) == "ddtrace")
    )
    if filtered and not filtered.endswith("\n"):
        filtered += "\n"
    _atomic_write(path, f"{filtered}-e .\n")
    return relative


def prepare_environment(
    root: Path,
    *,
    suite: str,
    environment_id: str,
    lockfile: Path,
    install_project: bool,
) -> PreparedEnvironment:
    lock_path = lockfile if lockfile.is_absolute() else root / lockfile
    if not lock_path.is_file():
        raise UvTestEnvironmentError(f"uv lockfile does not exist: {lockfile}")

    contents = lock_path.read_text()
    package_hash = package_content_hash(contents)
    project_hash = None
    if install_project:
        project_file = root / "pyproject.toml"
        if not project_file.is_file():
            raise UvTestEnvironmentError(f"project metadata does not exist: {project_file}")
        project_hash = hashlib.sha256(project_file.read_bytes()).hexdigest()[:12]
    suite_path = (part.replace(":", "-") for part in suite.split("::"))
    path = _CACHE_ROOT.joinpath(*suite_path, f"{environment_id}-{package_hash}")
    requirements = (
        _editable_requirements(root, lock_path, contents, package_hash)
        if install_project
        else lock_path.relative_to(root)
    )
    return PreparedEnvironment(
        path=path,
        requirements=requirements,
        package_hash=package_hash,
        packages=locked_packages(contents),
        direct_packages=_direct_packages(contents),
        install_project=install_project,
        project_hash=project_hash,
    )


def environment_commands(
    prepared: PreparedEnvironment,
    *,
    python: str,
    standard_editable: bool,
    exclude_newer: str | None = None,
) -> tuple[list[str], ...]:
    venv_python = prepared.path / "bin/python"
    synchronize = [
        "uv",
        "pip",
        "install",
        "--exact",
        "--python",
        str(venv_python),
        "--requirements",
        str(prepared.requirements),
        "--exclude-newer",
        exclude_newer or cooldown_cutoff(),
        "--strict",
        "--no-progress",
    ]
    if prepared.install_project and not standard_editable:
        synchronize.extend(["--config-settings-package", "ddtrace:editable_mode=compat"])
    return (
        [
            "uv",
            "venv",
            "--clear",
            "--relocatable",
            "--python",
            python,
            "--no-python-downloads",
            str(prepared.path),
        ],
        synchronize,
    )


def _installed_distributions(venv: Path) -> tuple[tuple[str, str], ...] | None:
    site_packages = list(venv.glob("lib/python*/site-packages"))
    if len(site_packages) != 1:
        return None

    distributions = []
    for metadata in site_packages[0].glob("*.dist-info/METADATA"):
        name = None
        version = None
        try:
            for line in metadata.read_text(errors="replace").splitlines():
                if line.startswith("Name: "):
                    name = normalize_package_name(line.removeprefix("Name: ").strip())
                elif line.startswith("Version: "):
                    version = line.removeprefix("Version: ").strip()
                if name is not None and version is not None:
                    break
        except OSError:
            return None
        if name is None or version is None:
            return None
        distributions.append((name, version))
    return tuple(sorted(distributions))


def _matches_lock(prepared: PreparedEnvironment, distributions: tuple[tuple[str, str], ...]) -> bool:
    installed = dict(distributions)
    if len(installed) != len(distributions):
        return False

    expected = dict(prepared.packages)
    if prepared.install_project:
        expected.pop("ddtrace", None)
    if any(installed.get(name) != version for name, version in expected.items()):
        return False
    if any(name not in installed for name in prepared.direct_packages):
        return False

    required_names = set(expected) | set(prepared.direct_packages)
    if prepared.install_project:
        required_names.add("ddtrace")
        return required_names <= installed.keys()
    return installed.keys() == required_names


def _environment_structure_exists(venv: Path) -> bool:
    python = venv / "bin/python"
    return (venv / "pyvenv.cfg").is_file() and (python.exists() or python.is_symlink())


def _state_identity(prepared: PreparedEnvironment, python: str, standard_editable: bool) -> dict[str, object]:
    return {
        "state_version": _STATE_VERSION,
        "package_hash": prepared.package_hash,
        "project_hash": prepared.project_hash,
        "python": python,
        "install_project": prepared.install_project,
        "editable_mode": "none" if not prepared.install_project else "standard" if standard_editable else "compat",
    }


def environment_is_current(
    root: Path,
    prepared: PreparedEnvironment,
    *,
    python: str,
    standard_editable: bool,
) -> bool:
    venv = root / prepared.path
    if not _environment_structure_exists(venv):
        return False

    try:
        state = json.loads((venv / _STATE_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    identity = _state_identity(prepared, python, standard_editable)
    if any(state.get(key) != value for key, value in identity.items()):
        return False

    distributions = _installed_distributions(venv)
    if distributions is None or not _matches_lock(prepared, distributions):
        return False
    return state.get("distributions") == [list(distribution) for distribution in distributions]


def _invalidate_environment(root: Path, prepared: PreparedEnvironment) -> None:
    (root / prepared.path / _STATE_FILE).unlink(missing_ok=True)


def _mark_environment_current(
    root: Path,
    prepared: PreparedEnvironment,
    *,
    python: str,
    standard_editable: bool,
) -> None:
    venv = root / prepared.path
    if not _environment_structure_exists(venv):
        raise UvTestEnvironmentError(f"uv created an incomplete environment: {prepared.path}")
    distributions = _installed_distributions(venv)
    if distributions is None or not _matches_lock(prepared, distributions):
        raise UvTestEnvironmentError(f"installed packages do not match the uv lock: {prepared.requirements}")

    state = _state_identity(prepared, python, standard_editable)
    state["distributions"] = [list(distribution) for distribution in distributions]
    _atomic_write(venv / _STATE_FILE, json.dumps(state, sort_keys=True) + "\n")


@contextmanager
def _environment_lock(root: Path, prepared: PreparedEnvironment) -> Iterator[None]:
    venv = root / prepared.path
    lock_path = venv.with_name(f".{venv.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def ensure_environment(
    root: Path,
    prepared: PreparedEnvironment,
    *,
    python: str,
    standard_editable: bool,
    run: Callable[[list[str]], None],
) -> bool:
    """Ensure an exact environment, returning True when it was rebuilt."""
    with _environment_lock(root, prepared):
        if environment_is_current(
            root,
            prepared,
            python=python,
            standard_editable=standard_editable,
        ):
            return False

        _invalidate_environment(root, prepared)
        for command in environment_commands(
            prepared,
            python=python,
            standard_editable=standard_editable,
        ):
            run(command)

        # AIDEV-NOTE: Record current state only after exact sync and post-install validation.
        # This marker is the guard against reusing partial or failed builds.
        _mark_environment_current(
            root,
            prepared,
            python=python,
            standard_editable=standard_editable,
        )
        return True
