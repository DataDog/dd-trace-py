# This file is part of "echion" which is released under MIT.
#
# Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

import argparse
import os
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

from echion._version import __version__


def detach(pid: int) -> None:
    from hypno import inject_py

    script = dedent(
        """
        from echion.bootstrap.attach import detach
        detach()
        """
    ).strip()

    inject_py(
        pid,
        script,
    )


def attach(args: argparse.Namespace) -> None:
    from hypno import inject_py

    pid = args.pid or args.where

    try:
        pipe_name = None
        if args.where:
            pipe_name = Path(tempfile.gettempdir()) / f"echion-{pid}"
            os.mkfifo(pipe_name)
            # This named pipe is likely created by the superuser, so we need to
            # make it writable by everyone to allow the target process to write
            # to it.
            os.chmod(pipe_name, 0o666)

        script = dedent(
            f"""
            from echion.bootstrap.attach import attach
            attach({args.__dict__!r}, {repr(str(pipe_name)) if pipe_name is not None else str(None)})
            """
        ).strip()

        inject_py(pid, script)

        try:
            end = None
            if args.exposure:
                from time import monotonic as time

                end = time() + args.exposure

            while not args.where:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                if end is not None and time() > end:
                    break
                os.sched_yield()

        except (KeyboardInterrupt, ProcessLookupError):
            pass

        # Read the output
        if args.where and pipe_name is not None and pipe_name.exists():
            with pipe_name.open("r") as f:
                while True:
                    line = f.readline()
                    print(line, end="")
                    if not line:
                        break

        detach(pid)

    finally:
        if args.where and pipe_name is not None and pipe_name.exists():
            pipe_name.unlink()


def microseconds(v: str) -> int:
    try:
        if v.endswith("ms"):
            return int(v[:-2]) * 1000
        if v.endswith("s"):
            return int(v[:-1]) * 1000000
        return int(v)
    except Exception as e:
        raise ValueError("Invalid interval: %s" % v) from e


def main() -> None:
    parser = argparse.ArgumentParser(
        description="In-process CPython frame stack sampler",
        prog="echion",
    )
    parser.add_argument(
        "command", nargs=argparse.REMAINDER, type=str, help="Command string to execute."
    )
    parser.add_argument(
        "-i",
        "--interval",
        help="sampling interval in microseconds",
        default=1000,
        type=microseconds,
    )
    parser.add_argument(
        "-c",
        "--cpu",
        help="sample on-CPU stacks only",
        action="store_true",
    )
    parser.add_argument(
        "-x",
        "--exposure",
        help="exposure time, in seconds",
        type=int,
    )
    parser.add_argument(
        "-m",
        "--memory",
        help="Collect memory allocation events",
        action="store_true",
    )
    parser.add_argument(
        "-n",
        "--native",
        help="sample native stacks",
        action="store_true",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="output location (can use %%(pid) to insert the process ID)",
        type=str,
        default="%%(pid).echion",
    )
    parser.add_argument(
        "-p",
        "--pid",
        help="Attach to the process with the given PID",
        type=int,
    )
    parser.add_argument(
        "-s",
        "--stealth",
        help="stealth mode (sampler thread is not accounted for)",
        action="store_true",
    )
    parser.add_argument(
        "-w",
        "--where",
        help="where mode: display thread stacks of the given process",
        type=int,
    )
    parser.add_argument(
        "-f",
        "--max-file-descriptors",
        help="maximum number of file descriptors to use to track thread running statuses, only for Linux",
        type=int,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="verbose logging",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version="%(prog)s " + __version__,
    )

    try:
        args = parser.parse_args()
    except Exception as e:
        print("echion: %s" % e)
        parser.print_usage()
        sys.exit(1)

    # TODO: Validate arguments

    env = os.environ.copy()

    env["ECHION_INTERVAL"] = str(args.interval)
    env["ECHION_CPU"] = str(int(bool(args.cpu)))
    env["ECHION_MEMORY"] = str(int(bool(args.memory)))
    env["ECHION_NATIVE"] = str(int(bool(args.native)))
    env["ECHION_OUTPUT"] = args.output.replace("%%(pid)", str(os.getpid()))
    env["ECHION_STEALTH"] = str(int(bool(args.stealth)))
    env["ECHION_WHERE"] = str(args.where or "")

    if args.pid or args.where:
        try:
            attach(args)
        except Exception as e:
            print("Failed to attach to process %d: %s" % (args.pid or args.where, e))
            sys.exit(1)
        return

    root_dir = Path(__file__).parent

    bootstrap_dir = root_dir / "bootstrap"

    if not args.command:
        parser.print_help()
        sys.exit(1)

    executable = args.command[0]

    python_path = os.getenv("PYTHONPATH")
    env["PYTHONPATH"] = (
        os.path.pathsep.join((str(bootstrap_dir), python_path))
        if python_path
        else str(bootstrap_dir)
    )

    try:
        os.execvpe(executable, args.command, env)  # TODO: Cross-platform?
    except OSError:
        print(
            "echion: executable '%s' does not have executable permissions.\n"
            % executable
        )
        parser.print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
