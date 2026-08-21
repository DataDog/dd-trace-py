from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")
LOCK_ROOT = Path("tests/locks")


def lockfile_path(suite: str, environment_id: str) -> Path:
    """Return the repository-relative lock path for one concrete environment."""
    suite_path = (part.replace(":", "-") for part in suite.split("::"))
    return LOCK_ROOT.joinpath(*suite_path, f"{environment_id}.txt")


@dataclass(frozen=True)
class TestRun:
    """One command and environment executed in a test environment."""

    command: str
    env: tuple[tuple[str, str], ...] = ()

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.env)


@dataclass(frozen=True)
class TestEnvironment:
    """A concrete, runner-independent test dependency environment."""

    id: str
    suite: str
    name: str
    python: str
    platform: str = "linux"
    direct_dependencies: tuple[str, ...] = ()
    dependency_groups: tuple[str, ...] = ()
    runs: tuple[TestRun, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    services: tuple[str, ...] = ()
    snapshot: bool = False
    retry: int | None = None
    timeout: int | None = None
    parallelism: int | None = None
    environments_per_job: int | None = None
    gpu: bool = False
    skip_pip_cache: bool = False
    install_project: bool = True
    lockfile: Path | None = None
    ordinal: int = 0

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.env)

    @property
    def command(self) -> str:
        return self.runs[0].command if self.runs else ""

    @property
    def display_name(self) -> str:
        packages = self._display_dependencies()
        if packages:
            return f"Python {self.python}, {', '.join(packages)}"
        return f"Python {self.python}"

    def _display_dependencies(self) -> list[str]:
        requirements = {}
        for requirement in self.direct_dependencies:
            match = _REQUIREMENT_NAME.match(requirement)
            if match:
                requirements[match.group(1).lower().replace("_", "-")] = requirement

        names = self.name.split(":")
        aliases = {
            "mysql": ("mysqlclient", "mysql-connector-python"),
            "psycopg2": ("psycopg2-binary",),
            "redis": ("redis-py",),
        }
        selected = []
        for name in names:
            normalized = name.lower().replace("_", "-")
            candidates = (normalized, *aliases.get(normalized, ()))
            for candidate in candidates:
                if selected_requirement := requirements.get(candidate):
                    selected.append(selected_requirement)
                    break
        return selected
