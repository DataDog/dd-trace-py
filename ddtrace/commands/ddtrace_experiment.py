#!/usr/bin/env python
"""``ddtrace-experiment`` — run inline experiments (trace-seeded local regression).

The explicit, out-of-band trigger for the ``@experiment_start`` / ``@experiment_end``
decorators. The typical loop has two steps so a code change can be detected:

    # 1. capture a baseline from the current (known-good) code
    ddtrace-experiment capture myapp:generate_traffic
    # 2. ...edit your prompt/model/logic...
    # 3. replay the current code against the baseline
    ddtrace-experiment replay myapp --comparator structural

Activation is positive and explicit — the decorators are inert unless this command runs,
so they are safe to leave in production code. As a one-way fail-safe this command also
refuses to run when it looks like production (no TTY and ``DD_ENV=prod``).

The baseline is persisted between the two invocations (default: ``.llmobs_experiments.json``).
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

    cap = sub.add_parser("capture", help="run the entrypoint and persist a baseline")
    cap.add_argument("target", help="module:entrypoint that drives the app (e.g. myapp:generate_traffic)")
    cap.add_argument("--out", default=None, help="baseline file to write (default: .llmobs_experiments.json)")

    rep = sub.add_parser("replay", help="replay the current code against a persisted baseline")
    rep.add_argument("target", help="module to import (registers the decorated subjects)")
    rep.add_argument(
        "--in", dest="infile", default=None, help="baseline file to read (default: .llmobs_experiments.json)"
    )
    rep.add_argument(
        "--comparator", default="structural", choices=["exact", "structural", "ignoring"], help="default: structural"
    )
    rep.add_argument("--ignore", default="", help="comma-separated keys to ignore (implies the 'ignoring' comparator)")

    lst = sub.add_parser("list", help="import the target and list registered experiment subjects")
    lst.add_argument("target", help="module[:entrypoint] to import")
    return parser


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if _smells_like_prod():
        print("ddtrace-experiment: refusing to run — looks like production (no TTY and DD_ENV=prod).", file=sys.stderr)
        sys.exit(2)

    # Imported lazily so the prod fail-safe runs before any user/ddtrace code is imported.
    from ddtrace.llmobs import _inline_experiment as ie
    from ddtrace.llmobs import _inline_experiment_runner as runner

    if args.command == "list":
        _import_target(args.target)
        names = ie.registered_experiments()
        print("registered experiment subjects:" if names else "no experiment subjects registered by %r" % args.target)
        for n in names:
            print("  - %s" % n)
        return

    if args.command == "capture":
        _, entry = _import_target(args.target)  # registers subjects
        if entry is None:
            print("capture needs 'module:entrypoint' to drive the app (e.g. myapp:generate_traffic).", file=sys.stderr)
            sys.exit(2)
        ie._set_mode(ie.Mode.CAPTURE)
        result = entry()
        if inspect.iscoroutine(result):
            asyncio.run(result)
        ie._set_mode(ie.Mode.OFF)
        out = args.out or runner.DEFAULT_BASELINE_PATH
        data = runner.save_baselines(out)
        n = sum(len(v) for v in data.values())
        print("captured %d case(s) across %d experiment(s) -> %s" % (n, len(data), os.path.abspath(out)))
        return

    # replay
    _import_target(args.target)  # registers subjects (start fns needed for re-invocation)
    infile = args.infile or runner.DEFAULT_BASELINE_PATH
    if not os.path.exists(infile):
        print("no baseline at %s — run `ddtrace-experiment capture` first." % infile, file=sys.stderr)
        sys.exit(2)
    baselines = runner.load_baselines(infile)
    ignore = [k for k in args.ignore.split(",") if k]
    comparator = runner.comparator_from_spec(args.comparator, ignore)

    ie._set_mode(ie.Mode.REPLAY)
    total = {}
    for name, cases in baselines.items():
        if name not in ie.registered_experiments():
            print("  (skipping %r — not registered by %r)" % (name, args.target))
            continue
        counts = _print_report(name, runner.replay(name, comparator, cases=cases))
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
    ie._set_mode(ie.Mode.OFF)

    # CI-friendly: non-zero exit if anything changed, errored, or never reached its end.
    sys.exit(1 if (total.get("CHANGED") or total.get("ERROR") or total.get("NO_END")) else 0)


if __name__ == "__main__":
    main()
