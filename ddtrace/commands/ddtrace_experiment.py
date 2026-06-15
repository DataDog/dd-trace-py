#!/usr/bin/env python
"""``ddtrace-experiment`` — run inline experiments (trace-seeded local regression).

This is the explicit, out-of-band trigger for the ``@experiment_start`` /
``@experiment_end`` decorators. It activates experiment mode *in-process*, imports the
target module (which registers the decorated subjects), captures a baseline by running
the app's entrypoint, then replays the current code against that baseline and reports
MATCH / CHANGED per case.

Activation is positive and explicit — the decorators are inert unless this command runs,
so they are safe to leave in production code. As a one-way fail-safe this command also
refuses to run when it looks like production (no TTY and ``DD_ENV=prod``); the fail-safe
can only tighten the gate, never loosen it.

Examples::

    ddtrace-experiment run myapp.entrypoint:generate_traffic
    ddtrace-experiment run myapp.entrypoint:generate_traffic --comparator structural
    ddtrace-experiment run myapp.entrypoint:generate_traffic --ignore generated_at,request_id
    ddtrace-experiment list myapp.entrypoint
"""

import argparse
import asyncio
import importlib
import inspect
import os
import sys


def _smells_like_prod():
    try:
        not_a_tty = not sys.stdout.isatty()
    except Exception:
        not_a_tty = False
    return not_a_tty and os.environ.get("DD_ENV", "").lower() in ("prod", "production")


def _import_target(target):
    """Resolve 'module' or 'module:callable'; importing registers decorated subjects."""
    mod_name, _, attr = target.partition(":")
    mod = importlib.import_module(mod_name)
    entry = getattr(mod, attr) if attr else None
    return mod, entry


def _trunc(v, n=34):
    import json

    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_report(name, rows):
    counts = {}
    print("\n  experiment: %s   (%d case(s))" % (name, len(rows)))
    print("  " + "-" * 92)
    print("  %-8s %-32s %-22s %-22s" % ("status", "input", "recorded", "new"))
    print("  " + "-" * 92)
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(
            "  %-8s %-32s %-22s %-22s"
            % (r["status"], _trunc(r["input"], 32), _trunc(r["recorded"], 22), _trunc(r["new"], 22))
        )
    print("  " + "-" * 92)
    print("  " + "  ".join("%s=%d" % (k, v) for k, v in counts.items()) + "\n")
    return counts


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="ddtrace-experiment", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="capture a baseline (run the entrypoint) then replay the current code")
    run_p.add_argument("target", help="module:entrypoint that drives the app (e.g. myapp:generate_traffic)")
    run_p.add_argument(
        "--comparator",
        default="structural",
        choices=["exact", "structural", "ignoring"],
        help="how to compare new output to baseline (default: structural)",
    )
    run_p.add_argument(
        "--ignore", default="", help="comma-separated keys to ignore (implies the 'ignoring' comparator)"
    )

    list_p = sub.add_parser("list", help="import the target and list registered experiment subjects")
    list_p.add_argument("target", help="module[:entrypoint] to import")
    return parser


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if _smells_like_prod():
        print("ddtrace-experiment: refusing to run — looks like production (no TTY and DD_ENV=prod).", file=sys.stderr)
        sys.exit(2)

    # Imported lazily so the prod fail-safe runs before any user code is imported.
    from ddtrace.llmobs import _inline_experiment as ie
    from ddtrace.llmobs import _inline_experiment_runner as runner

    if args.command == "list":
        _import_target(args.target)
        names = ie.registered_experiments()
        if not names:
            print("no experiment subjects registered by %r" % args.target)
        else:
            print("registered experiment subjects:")
            for n in names:
                print("  - %s" % n)
        return

    # run: capture -> replay, in-process.
    _, entry = _import_target(args.target)  # registers subjects
    if entry is None:
        print(
            "ddtrace-experiment run needs 'module:entrypoint' to drive a capture (e.g. myapp:generate_traffic).",
            file=sys.stderr,
        )
        sys.exit(2)

    ie._set_mode(ie.Mode.CAPTURE)
    result = entry()
    if inspect.iscoroutine(result):
        asyncio.run(result)

    ignore = [k for k in args.ignore.split(",") if k]
    comparator = runner.comparator_from_spec(args.comparator, ignore)

    ie._set_mode(ie.Mode.REPLAY)
    total = {"MATCH": 0, "CHANGED": 0, "ERROR": 0, "NO_END": 0}
    for name in ie.registered_experiments():
        counts = _print_report(name, runner.replay(name, comparator))
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v

    ie._set_mode(ie.Mode.OFF)
    # Non-zero exit if anything changed or errored, so this is CI-friendly.
    sys.exit(1 if (total.get("CHANGED") or total.get("ERROR") or total.get("NO_END")) else 0)


if __name__ == "__main__":
    main()
