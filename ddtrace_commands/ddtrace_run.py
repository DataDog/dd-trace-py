#!/usr/bin/env python
import argparse
import importlib.util
import logging
import os
import shutil
import sys
import typing  # noqa:F401


env = os.environ


def _find_executable(args: typing.Optional[argparse.Namespace]) -> typing.Optional[str]:
    if args is None:
        return None
    command = typing.cast(str, args.command[0])
    if os.path.isfile(command):
        return command
    return shutil.which(command)


# The launcher must not import ddtrace before replacing itself with the application.
log = logging.getLogger("ddtrace.commands.ddtrace_run")

USAGE = """
Execute the given Python command after configuring it to emit Datadog traces
and profiles.


Examples
ddtrace-run python app.py
ddtrace-run gunicorn myproject.wsgi
"""


def _add_bootstrap_to_pythonpath(bootstrap_dir: str) -> None:
    """
    Add our bootstrap directory to the head of $PYTHONPATH to ensure
    it is loaded before program code
    """
    python_path = env.get("PYTHONPATH", "")

    if python_path:
        new_path = "%s%s%s" % (bootstrap_dir, os.path.pathsep, env["PYTHONPATH"])
        env["PYTHONPATH"] = new_path
    else:
        env["PYTHONPATH"] = bootstrap_dir


class _VersionAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: typing.Any,
        option_string: typing.Optional[str] = None,
    ) -> None:
        from ddtrace.version import __version__

        parser._print_message("%s %s\n" % (parser.prog, __version__), sys.stdout)
        parser.exit()


def _get_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=USAGE,
        prog="ddtrace-run",
        usage="ddtrace-run <your usual python command>",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, type=str, help="Command string to execute.")
    parser.add_argument("-d", "--debug", help="enable debug mode (disabled by default)", action="store_true")
    parser.add_argument(
        "-i",
        "--info",
        help=(
            "print library info useful for debugging. Only reflects configurations made via environment "
            "variables, not those made in code."
        ),
        action="store_true",
    )
    parser.add_argument("-p", "--profiling", help="enable profiling (disabled by default)", action="store_true")
    parser.add_argument("-v", "--version", action=_VersionAction, nargs=0, help="show program version and exit")
    parser.add_argument("-nc", "--colorless", help="print output of command without color", action="store_true")
    return parser


def _prepare_env(parser: argparse.ArgumentParser) -> argparse.Namespace:
    args = parser.parse_args()
    if args.profiling:
        env["DD_PROFILING_ENABLED"] = "true"

    if args.debug or env.get("DD_TRACE_DEBUG", "").lower() in ("true", "1"):
        logging.basicConfig(level=logging.DEBUG)
        env["DD_TRACE_DEBUG"] = "true"

    if args.info:
        # Inline imports for performance.
        from ddtrace.internal.debug import pretty_collect
        from ddtrace.trace import tracer

        print(pretty_collect(tracer, color=not args.colorless))
        sys.exit(0)

    spec = importlib.util.find_spec("ddtrace")
    if spec is None or spec.origin is None:
        raise ImportError("Cannot find the ddtrace package")
    root_dir = os.path.dirname(spec.origin)
    log.debug("ddtrace root: %s", root_dir)

    bootstrap_dir = os.path.join(root_dir, "bootstrap")
    log.debug("ddtrace bootstrap: %s", bootstrap_dir)

    _add_bootstrap_to_pythonpath(bootstrap_dir)
    log.debug("PYTHONPATH: %s", env["PYTHONPATH"])
    log.debug("sys.path: %s", sys.path)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    return args


def main() -> None:
    parser = None
    args = None
    executable = None
    try:
        parser = _get_arg_parser()
        args = _prepare_env(parser)
        executable = _find_executable(args)
    except Exception:
        log.warning("error bootstrapping Datadog tracing", exc_info=True)

    if executable is None or args is None:
        print("ddtrace-run: failed to bootstrap: args '%s'.\n" % args)
        if parser is not None:
            parser.print_usage()
        sys.exit(1)

    log.debug("program executable: %s", executable)

    if os.path.basename(executable) == "uwsgi":
        print(
            "ddtrace-run has known compatibility issues with uWSGI where the "
            "tracer is not started properly in uWSGI workers which can cause "
            "broken behavior. It is recommended you remove ddtrace-run and "
            "update your uWSGI configuration following "
            "https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#uwsgi."
        )

    try:
        os.execl(executable, executable, *args.command[1:])
    except PermissionError:
        print("ddtrace-run: permission error while launching '%s'" % executable)
        print("Did you mean `ddtrace-run python %s`?" % executable)
        sys.exit(1)
    except Exception:
        print("ddtrace-run: error launching '%s'" % executable)
        raise

    sys.exit(0)


if __name__ == "__main__":
    main()
