from __future__ import annotations

import argparse
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
import concurrent.futures
import datetime as dt
from pathlib import Path
import re
import subprocess
import tempfile

from tests.environment import LOCK_ROOT
from tests.environment import TestEnvironment
from tests.internal.riot_seed_locks import RIOT_SEED_LOCKS
from tests.matrix import expand_declared_matrices


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Keep this aligned with the existing freshness and lock-validation policy.
COOLDOWN_DAYS = 2


class LockError(RuntimeError):
    """Raised when concrete test-environment locks cannot be generated."""


def cooldown_cutoff(now: dt.datetime | None = None) -> str:
    """Return uv's UTC cutoff timestamp for the package cooldown policy."""
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise LockError("cooldown timestamp must be timezone-aware")
    cutoff = current.astimezone(dt.timezone.utc) - dt.timedelta(days=COOLDOWN_DAYS)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_suites(matrices: Mapping[str, tuple[TestEnvironment, ...]], requested: Sequence[str]) -> tuple[str, ...]:
    if not requested:
        return tuple(sorted(matrices))

    resolved = []
    for name in requested:
        if name in matrices:
            resolved.append(name)
            continue
        candidates = [suite for suite in matrices if suite.rsplit("::", 1)[-1] == name]
        if not candidates:
            raise LockError(f"suite has no declarative matrix: {name}")
        if len(candidates) > 1:
            choices = ", ".join(sorted(candidates))
            raise LockError(f"ambiguous suite {name!r}; choose one of: {choices}")
        resolved.append(candidates[0])
    return tuple(dict.fromkeys(resolved))


def select_environments(
    suites: Mapping[str, Mapping[str, object]],
    defaults: Mapping[str, object],
    requested: Sequence[str] = (),
) -> tuple[tuple[TestEnvironment, ...], tuple[str, ...]]:
    """Expand and select concrete environments using full or short suite names."""
    matrices = expand_declared_matrices(suites, defaults, nightly=False)
    selected_suites = _resolve_suites(matrices, requested)
    environments = tuple(
        environment
        for suite in selected_suites
        for environment in sorted(matrices[suite], key=lambda item: item.ordinal)
    )
    return environments, selected_suites


def match_riot_seed_locks(
    environments: Sequence[TestEnvironment],
    *,
    root: Path = PROJECT_ROOT,
    require_all: bool = True,
) -> dict[tuple[str, str], Path]:
    """Map descriptive environment IDs to their checked-in Riot seed locks."""
    seeds = {}
    for environment in environments:
        riot_id = RIOT_SEED_LOCKS.get(environment.suite, {}).get(environment.id)
        if not isinstance(riot_id, str) or re.fullmatch(r"[0-9a-f]{7}", riot_id) is None:
            if require_all:
                raise LockError(f"no matching Riot lock for {environment.suite}/{environment.id}")
            continue
        seed = Path(".riot/requirements") / f"{riot_id}.txt"
        if not (root / seed).is_file():
            raise LockError(f"Riot seed lock does not exist: {seed}")
        seeds[(environment.suite, environment.id)] = seed
    return seeds


def compile_environment(
    environment: TestEnvironment,
    *,
    root: Path = PROJECT_ROOT,
    exclude_newer: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Compile one concrete environment and return its requirements-style lock."""
    if environment.lockfile is None:
        raise LockError(f"environment has no lockfile path: {environment.id}")
    if not environment.direct_dependencies:
        raise LockError(f"environment has no dependencies: {environment.id}")

    with tempfile.TemporaryDirectory(prefix=f"ddtrace-{environment.id}-") as temporary:
        temporary_path = Path(temporary)
        requirements = temporary_path / "requirements.in"
        output = temporary_path / "requirements.txt"
        requirements.write_text("\n".join(sorted(environment.direct_dependencies, key=str.casefold)) + "\n")
        command = [
            "uv",
            "pip",
            "compile",
            "--python-version",
            environment.python,
            "--python-platform",
            environment.platform,
            "--exclude-newer",
            exclude_newer or cooldown_cutoff(),
            "--no-annotate",
            "--no-header",
            "--no-progress",
            "--no-python-downloads",
            "--no-sources",
            "--output-file",
            str(output),
            str(requirements),
        ]
        try:
            run(command, cwd=root, check=True, text=True, capture_output=True)
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "").strip()
            suffix = f"\n{details}" if details else ""
            raise LockError(f"failed to lock {environment.suite}/{environment.id}{suffix}") from error
        return output.read_text()


def _write_lock(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _prune_locks(expected: set[Path], selected_suites: Sequence[str], *, root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    pruned = []
    for suite in selected_suites:
        suite_root = root / LOCK_ROOT.joinpath(*suite.split("::"))
        if not suite_root.exists():
            continue
        for path in sorted(suite_root.rglob("*.txt")):
            if path.relative_to(root) not in expected:
                path.unlink()
                pruned.append(path.relative_to(root))
        for directory in sorted((item for item in suite_root.rglob("*") if item.is_dir()), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
    return tuple(pruned)


def generate_locks(
    suites: Mapping[str, Mapping[str, object]],
    defaults: Mapping[str, object],
    requested: Sequence[str] = (),
    *,
    root: Path = PROJECT_ROOT,
    jobs: int = 4,
    exclude_newer: str | None = None,
    seed_locks: Mapping[tuple[str, str], Path] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Compile, atomically write, and prune locks for the selected suites."""
    environments, selected_suites = select_environments(suites, defaults, requested)
    if not environments:
        raise LockError("no concrete test environments selected")

    compiled: dict[TestEnvironment, str] = {}
    pending = []
    for environment in environments:
        key = (environment.suite, environment.id)
        seed = seed_locks.get(key) if seed_locks is not None else None
        if seed is not None:
            seed_path = root / seed
            if not seed_path.is_file():
                raise LockError(f"Riot seed lock does not exist: {seed}")
            compiled[environment] = seed_path.read_text()
        else:
            pending.append(environment)

    if pending:
        cutoff = exclude_newer or cooldown_cutoff()
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
            futures = {
                executor.submit(
                    compile_environment,
                    environment,
                    root=root,
                    exclude_newer=cutoff,
                    run=run,
                ): environment
                for environment in pending
            }
            for future in concurrent.futures.as_completed(futures):
                environment = futures[future]
                try:
                    compiled[environment] = future.result()
                except LockError as error:
                    errors.append(error)
        if errors:
            raise LockError("\n\n".join(str(error) for error in errors))

    written = []
    for environment in environments:
        assert environment.lockfile is not None
        _write_lock(root / environment.lockfile, compiled[environment])
        written.append(environment.lockfile)
    pruned = _prune_locks(set(written), selected_suites, root=root)
    return tuple(written), pruned


def main(argv: Sequence[str] | None = None) -> int:
    from tests.suitespec import get_matrix_defaults
    from tests.suitespec import get_suites

    parser = argparse.ArgumentParser(description="Manage concrete uv locks for test environments.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="List concrete environment IDs for selected suites.")
    list_parser.add_argument("suites", nargs="+", help="Full or unambiguous short suite names.")
    lock_parser = subparsers.add_parser("lock", help="Generate and prune concrete test-environment locks.")
    lock_parser.add_argument("suites", nargs="*", help="Full or unambiguous short suite names; defaults to all.")
    lock_parser.add_argument("--jobs", type=int, default=4, help="Number of concurrent uv resolvers (default: 4).")
    args = parser.parse_args(argv)

    try:
        suites = get_suites()
        defaults = get_matrix_defaults()
        environments, _ = select_environments(suites, defaults, args.suites)
        if args.command == "list":
            for environment in environments:
                print(environment.id)
            return 0
        seeds = match_riot_seed_locks(environments, require_all=False)
        written, pruned = generate_locks(
            suites,
            defaults,
            args.suites,
            jobs=args.jobs,
            seed_locks=seeds,
        )
    except LockError as error:
        parser.error(str(error))
    print(f"Locked {len(written)} concrete environment(s); pruned {len(pruned)} obsolete lock(s).")
    return 0
