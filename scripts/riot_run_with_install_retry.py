#!/usr/bin/env python3
"""Retry riot venv installs that fail with CmdFailure / BrokenPipe.

Riot has no pip --retries hook. GitLab testrunner retry is 0 and must stay that
way: this wrapper retries only when riot reports a venv-dependency install
failure, not when pytest fails.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
import os
import subprocess  # nosec B404
import sys
from typing import IO


DEFAULT_INSTALL_ATTEMPTS: int = 3
_ENV_ATTEMPTS: str = "RIOT_INSTALL_RETRIES"
_VENV_INSTALL_FAILURE: str = "Failed to install venv dependencies"
_BROKEN_PIPE: str = "BrokenPipeError"


def attempts_from_env(env: Mapping[str, str]) -> int:
    raw: str = env.get(_ENV_ATTEMPTS, str(DEFAULT_INSTALL_ATTEMPTS))
    try:
        parsed: int = int(raw)
    except ValueError:
        return DEFAULT_INSTALL_ATTEMPTS
    return max(1, parsed)


def is_retryable_venv_install_failure(output: str) -> bool:
    # Riot wraps pip CmdFailure as this message. Test failures use
    # "Test failed with exit code" and must not retry (testrunner retry: 0).
    if "Test failed with exit code" in output:
        return False
    return _VENV_INSTALL_FAILURE in output or _BROKEN_PIPE in output


def run_command(argv: Sequence[str]) -> tuple[int, str]:
    proc: subprocess.Popen[str] = subprocess.Popen(  # nosec B603
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    stdout_pipe: IO[str] | None = proc.stdout
    if stdout_pipe is None:
        returncode: int = proc.wait()
        return returncode, ""
    for line in stdout_pipe:
        chunks.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    finished: int = proc.wait()
    return finished, "".join(chunks)


def run_with_install_retry(
    argv: Sequence[str],
    *,
    attempts: int,
    run: Callable[[Sequence[str]], tuple[int, str]] = run_command,
) -> int:
    last_code: int = 1
    for attempt in range(1, attempts + 1):
        result: tuple[int, str] = run(argv)
        last_code = result[0]
        output: str = result[1]
        if last_code == 0:
            return 0
        retryable: bool = is_retryable_venv_install_failure(output)
        if not retryable or attempt == attempts:
            return last_code
        print(
            f"riot venv install failed (attempt {attempt}/{attempts}); retrying",
            file=sys.stderr,
        )
    return last_code


def main(argv: Sequence[str] | None = None) -> int:
    args: list[str] = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(f"usage: {sys.argv[0]} <command>...", file=sys.stderr)
        return 2
    attempts: int = attempts_from_env(os.environ)
    return run_with_install_retry(args, attempts=attempts)


if __name__ == "__main__":
    sys.exit(main())
