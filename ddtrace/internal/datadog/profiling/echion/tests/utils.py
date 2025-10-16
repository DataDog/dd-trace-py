from itertools import count
import os
import sys
import typing as t
from pathlib import Path
from shutil import which
from subprocess import PIPE
from subprocess import CalledProcessError
from subprocess import CompletedProcess
from subprocess import Popen
from subprocess import run
from time import sleep

import pytest
from austin.format.mojo import MojoFile


PY = sys.version_info[:2]
PROFILES = Path("profiles")
PROFILES.mkdir(exist_ok=True)


class DataSummary:
    def __init__(self, data: MojoFile) -> None:
        self.data = data
        self.metadata = data.metadata

        self.threads: t.Dict[str, dict] = {}
        self.total_metric = 0
        self.nsamples = 0

        for sample in data.samples:
            self.nsamples += 1
            frames = sample.frames
            v = sample.metrics[0].value

            if not sample.thread or not v:
                continue

            self.total_metric += v

            stacks = self.threads.setdefault(f"{sample.iid}:{sample.thread}", {})

            stack = tuple((f.scope.string.value, f.line) for f in frames)
            stacks[stack] = stacks.get(stack, 0) + v

            fstack = tuple(f.scope.string.value for f in frames)
            stacks[fstack] = stacks.get(fstack, 0) + v

    @property
    def nthreads(self):
        return len(self.threads)

    def query(self, thread_name, frames):
        try:
            stacks = self.threads[thread_name]
        except KeyError as e:
            raise AssertionError(
                f"Expected thread {thread_name}, found {list(self.threads.keys())}"
            ) from e
        for stack in stacks:
            for i in range(0, len(stack) - len(frames) + 1):
                if stack[i : i + len(frames)] == frames:
                    return stacks[stack]
        return None

    def assert_stack(self, thread, frames, predicate):
        try:
            stack = self.threads[thread][frames]
            assert predicate(stack), stack
        except KeyError:
            if thread not in self.threads:
                raise AssertionError(
                    f"Expected thread {thread}, found {self.threads.keys()}"
                ) from None
            raise AssertionError(
                f"Expected stack {frames}, found {self.threads[thread].keys()}"
            ) from None

    def assert_substack(self, thread, frames, predicate):
        try:
            stacks = self.threads[thread]
            for stack in stacks:
                for i in range(0, len(stack) - len(frames) + 1):
                    substack = stack[i : i + len(frames)]
                    if substack == frames:
                        assert predicate(stacks[stack]), stacks[stack]
                        return
            else:
                raise AssertionError("No matching substack found")

        except KeyError:
            if thread not in self.threads:
                raise AssertionError(
                    f"Expected thread {thread}, found {self.threads.keys()}"
                ) from None
            raise AssertionError(
                f"Expected stack {frames}, found {self.threads[thread].keys()}"
            ) from None


def run_echion(*args: str) -> CompletedProcess:
    try:
        return run(
            [
                "echion",
                *args,
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
    except CalledProcessError as e:
        print(e.stdout.decode())
        print(e.stderr.decode())
        raise


def run_target(
    target: Path, *args: str
) -> t.Tuple[CompletedProcess, t.Optional[MojoFile]]:
    test_name = sys._getframe(1).f_code.co_name
    output_file = (PROFILES / test_name).with_suffix(".mojo")
    n = count(1)
    while output_file.exists():
        output_file = (PROFILES / f"{test_name}-{next(n)}").with_suffix(".mojo")

    result = run_echion(
        "-o",
        str(output_file),
        *args,
        sys.executable,
        "-m",
        f"tests.{target}",
    )

    if not output_file.is_file():
        return result, None

    m = MojoFile(output_file.open(mode="rb"))
    m.unwind()
    return result, m


def run_with_signal(target: Path, signal: int, delay: float, *args: str) -> Popen:
    p = Popen(
        [
            t.cast(str, which("echion")),
            *args,
            sys.executable,
            "-m",
            f"tests.{target}",
        ],
        stdout=PIPE,
        stderr=PIPE,
    )

    sleep(delay)

    p.send_signal(signal)

    p.wait()

    return p


stealth = pytest.mark.parametrize("stealth", [tuple(), ("--stealth",)])


if sys.platform == "win32":
    requires_sudo = no_sudo = lambda f: f
else:
    requires_sudo = pytest.mark.skipif(
        os.geteuid() != 0, reason="Requires superuser privileges"
    )
    no_sudo = pytest.mark.skipif(
        os.geteuid() == 0, reason="Must not have superuser privileges"
    )
