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
from typing import Literal


# Two days matches the cross-language supply-chain cooldown used by test locks.
COOLDOWN_DAYS = 2
_CACHE_ROOT = Path(".cache/uv-test-environments")
_STATE_FILE = ".ddtrace-test-environment.json"
_STATE_VERSION = 1
EditableMode = Literal["none", "compat", "prebuilt-editable"]


class UvTestEnvironmentError(RuntimeError):
    """Raised when a test environment cannot be prepared or validated."""


@dataclass(frozen=True)
class PreparedEnvironment:
    path: Path
    requirements: Path
    project_hash: str | None
    editable_mode: EditableMode


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


def _project_requirements(
    root: Path,
    lockfile: Path,
    contents: str,
    package_hash: str,
    project_hash: str,
    project_requirement: str,
) -> Path:
    relative = _CACHE_ROOT / ".requirements" / f"{lockfile.stem}-{package_hash}-{project_hash}.txt"
    path = root / relative
    filtered = "".join(
        line for line in contents.splitlines(keepends=True) if line.partition("==")[0].strip().casefold() != "ddtrace"
    )
    if filtered and not filtered.endswith("\n"):
        filtered += "\n"
    _atomic_write(path, f"{filtered}{project_requirement}\n")
    return relative


def prepare_environment(
    root: Path,
    *,
    environment_hash: str,
    lockfile: Path,
    install_project: bool,
    project_artifact: Path | None = None,
) -> PreparedEnvironment:
    lock_path = lockfile if lockfile.is_absolute() else root / lockfile
    if not lock_path.is_file():
        raise UvTestEnvironmentError(f"uv lockfile does not exist: {lockfile}")

    contents = lock_path.read_text()
    package_hash = _content_hash(contents.encode())
    project_hash = None
    project_requirement = None
    editable_mode: EditableMode = "none"
    if install_project:
        if project_artifact is not None:
            artifact_path = project_artifact if project_artifact.is_absolute() else root / project_artifact
            if not artifact_path.is_file():
                raise UvTestEnvironmentError(f"project artifact does not exist: {artifact_path}")
            artifact_path = artifact_path.resolve()
            artifact_stat = artifact_path.stat()
            artifact_identity = f"{artifact_path.name}:{artifact_stat.st_size}:{artifact_stat.st_mtime_ns}"
            project_hash = _content_hash(artifact_identity.encode())
            project_requirement = str(artifact_path)
            editable_mode = "prebuilt-editable"
        else:
            project_file = root / "pyproject.toml"
            if not project_file.is_file():
                raise UvTestEnvironmentError(f"project metadata does not exist: {project_file}")
            project_hash = _content_hash(project_file.read_bytes())
            project_requirement = "-e ."
            editable_mode = "compat"
    elif project_artifact is not None:
        raise UvTestEnvironmentError("a project artifact requires install_project")
    path = _CACHE_ROOT / f"{environment_hash}-{package_hash}"
    requirements = (
        _project_requirements(root, lock_path, contents, package_hash, project_hash, project_requirement)
        if install_project
        else lock_path.relative_to(root)
    )
    return PreparedEnvironment(
        path=path,
        requirements=requirements,
        project_hash=project_hash,
        editable_mode=editable_mode,
    )


def environment_commands(
    prepared: PreparedEnvironment,
    *,
    python: str,
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
    if prepared.editable_mode == "compat":
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


def _state_identity(prepared: PreparedEnvironment, python: str) -> dict[str, object]:
    return {
        "state_version": _STATE_VERSION,
        "project_hash": prepared.project_hash,
        "python": python,
        "editable_mode": prepared.editable_mode,
    }


def environment_is_current(
    root: Path,
    prepared: PreparedEnvironment,
    *,
    python: str,
) -> bool:
    venv = root / prepared.path
    if not _environment_structure_exists(venv):
        return False

    try:
        state = json.loads((venv / _STATE_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    identity = _state_identity(prepared, python)
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
) -> None:
    venv = root / prepared.path
    if not _environment_structure_exists(venv):
        raise UvTestEnvironmentError(f"uv created an incomplete environment: {prepared.path}")
    distributions = _installed_distributions(venv)
    if distributions is None:
        raise UvTestEnvironmentError(f"uv created an invalid environment: {prepared.path}")

    state = _state_identity(prepared, python)
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
    reuse_current: bool,
    run: Callable[[list[str]], None],
) -> bool:
    """Ensure an exact environment, returning True when it was rebuilt."""
    with _environment_lock(root, prepared):
        if reuse_current and environment_is_current(
            root,
            prepared,
            python=python,
        ):
            return False

        (root / prepared.path / _STATE_FILE).unlink(missing_ok=True)
        for command in environment_commands(
            prepared,
            python=python,
        ):
            run(command)

        # AIDEV-NOTE: Record current state only after exact sync and environment inspection.
        # This marker is the guard against reusing partial or failed builds.
        _mark_environment_current(
            root,
            prepared,
            python=python,
        )
        return True
