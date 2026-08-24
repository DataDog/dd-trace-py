from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
import fcntl
import hashlib
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
import tempfile
from typing import Iterator


# Two days matches the cross-language supply-chain cooldown used by test locks.
COOLDOWN_DAYS = 2
_CACHE_ROOT = Path(".cache/uv-test-environments")
_STATE_FILE = ".ddtrace-test-environment.json"
_STATE_VERSION = 1


class UvTestEnvironmentError(RuntimeError):
    """Raised when a test environment cannot be prepared or validated."""


@dataclass(frozen=True)
class PreparedEnvironment:
    path: Path
    requirements: Path
    install_project: bool
    project_hash: str | None


def _content_hash(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()[:12]


def cooldown_cutoff(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise UvTestEnvironmentError("cooldown timestamp must be timezone-aware")
    cutoff = current.astimezone(dt.timezone.utc) - dt.timedelta(days=COOLDOWN_DAYS)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        line for line in contents.splitlines(keepends=True) if line.partition("==")[0].strip().casefold() != "ddtrace"
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
    package_hash = _content_hash(contents.encode())
    project_hash = None
    if install_project:
        project_file = root / "pyproject.toml"
        if not project_file.is_file():
            raise UvTestEnvironmentError(f"project metadata does not exist: {project_file}")
        project_hash = _content_hash(project_file.read_bytes())
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
    for metadata_path in site_packages[0].glob("*.dist-info/METADATA"):
        try:
            distribution = importlib_metadata.Distribution.at(metadata_path.parent)
            name = distribution.metadata.get("Name")
            version = distribution.version
            if not name or not version:
                return None
            distributions.append((name.casefold(), version))
        except (KeyError, OSError):
            return None
    return tuple(sorted(distributions))


def _environment_structure_exists(venv: Path) -> bool:
    python = venv / "bin/python"
    return (venv / "pyvenv.cfg").is_file() and (python.exists() or python.is_symlink())


def _state_identity(prepared: PreparedEnvironment, python: str, standard_editable: bool) -> dict[str, object]:
    return {
        "state_version": _STATE_VERSION,
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
    if not isinstance(state, dict) or any(state.get(key) != value for key, value in identity.items()):
        return False

    distributions = _installed_distributions(venv)
    if distributions is None:
        return False
    return state.get("distributions") == [list(distribution) for distribution in distributions]


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
    if distributions is None:
        raise UvTestEnvironmentError(f"uv created an invalid environment: {prepared.path}")

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

        (root / prepared.path / _STATE_FILE).unlink(missing_ok=True)
        for command in environment_commands(
            prepared,
            python=python,
            standard_editable=standard_editable,
        ):
            run(command)

        # AIDEV-NOTE: Record current state only after exact sync and environment inspection.
        # This marker is the guard against reusing partial or failed builds.
        _mark_environment_current(
            root,
            prepared,
            python=python,
            standard_editable=standard_editable,
        )
        return True
