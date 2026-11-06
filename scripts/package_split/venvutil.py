"""Virtual-environment helpers for artifact, compatibility and migration gates.

Uses only the standard library (``venv`` + ``pip``) so the harness works on
developer machines and CI images without extra tooling. Installs that only
need local wheels pass ``--no-index --find-links`` so they never touch the
network.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import subprocess  # nosec B404
import sys
from typing import Any
from typing import Optional


class VenvError(Exception):
    pass


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _isolate_venv(venv_dir: Path) -> None:
    """Disable system-site-packages inheritance in the new venv.

    ``python -m venv`` inherits the include-system-site-packages flag from
    the interpreter that creates it. Gate venvs built from repository test
    environments would otherwise see the editable ddtrace install (and
    its pytest entry points), fabricating plugin/ownership conflicts.
    """
    config = venv_dir / "pyvenv.cfg"
    if not config.is_file():
        return
    lines = config.read_text(encoding="utf-8").splitlines()
    rewritten = []
    found = False
    for line in lines:
        if line.strip().lower().startswith("include-system-site-packages"):
            rewritten.append("include-system-site-packages = false")
            found = True
        else:
            rewritten.append(line)
    if not found:
        rewritten.append("include-system-site-packages = false")
    config.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def create_venv(venv_dir: Path) -> Path:
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(  # nosec B603
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise VenvError("venv creation failed: %s" % result.stderr.strip())
    _isolate_venv(venv_dir)
    return venv_python(venv_dir)


def pip_install(
    venv_dir: Path,
    requirements: Iterable[str],
    find_links: Optional[Iterable[Path]] = None,
    no_index: bool = False,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess[str]:
    """Install requirements into the venv; raise VenvError on failure."""
    command: list[str] = [str(venv_python(venv_dir)), "-m", "pip", "install", "--disable-pip-version-check"]
    if no_index:
        command.append("--no-index")
    for link in find_links or []:
        command += ["--find-links", str(link)]
    command.extend(requirements)
    result = subprocess.run(  # nosec B603
        command,
        capture_output=True,
        text=True,
        timeout=1200,
        cwd=str(cwd) if cwd else None,
    )
    if result.returncode != 0:
        raise VenvError("pip install failed: %s" % (result.stderr.strip() or result.stdout.strip()))
    return result


def pip_uninstall(venv_dir: Path, distributions: Iterable[str]) -> subprocess.CompletedProcess[str]:
    command = [str(venv_python(venv_dir)), "-m", "pip", "uninstall", "-y"]
    command.extend(distributions)
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)  # nosec B603
    if result.returncode != 0:
        raise VenvError("pip uninstall failed: %s" % result.stderr.strip())
    return result


def run_in_venv(
    venv_dir: Path,
    args: list[str],
    cwd: Optional[Path] = None,
    env_extra: Optional[dict[str, str]] = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command with the venv interpreter and a scrubbed environment.

    PYTHONPATH and other leakage vectors are removed so the child process
    resolves imports from the venv (and any explicit sys.path additions the
    probe itself makes), never from the source checkout.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["VIRTUAL_ENV"] = str(venv_dir)
    env["PATH"] = str(venv_python(venv_dir).parent) + os.pathsep + env.get("PATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(  # nosec B603
        [str(venv_python(venv_dir))] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        timeout=timeout,
    )


def installed_distributions(venv_dir: Path) -> dict[str, str]:
    """Map of installed distribution name -> exact version in the venv.

    Reads the name from METADATA rather than ``Distribution.name``: the
    attribute does not exist on importlib.metadata.PathDistribution in
    Python 3.9.
    """
    probe = (
        "import importlib.metadata, json;"
        "print(json.dumps({d.metadata['Name']: d.version for d in importlib.metadata.distributions()}))"
    )
    result = run_in_venv(venv_dir, ["-c", probe])
    if result.returncode != 0:
        raise VenvError("distribution listing failed: %s" % result.stderr.strip())
    import json

    loaded: Any = json.loads(result.stdout.strip())
    return {str(name): str(version) for name, version in loaded.items()}
