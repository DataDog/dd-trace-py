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

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import os
import sys
from typing import Any
from typing import Optional


def _smells_like_prod() -> bool:
    # ``env`` (not ``os.environ``) is the repo-mandated accessor; imported lazily so this
    # one-way fail-safe still runs before the user's app/LLMObs is imported.
    from ddtrace.internal.settings import env

    try:
        not_a_tty = not sys.stdout.isatty()
    except Exception:
        not_a_tty = False
    return not_a_tty and env.get("DD_ENV", "").lower() in ("prod", "production")


def _load_env_file(path: str) -> int:
    """Load ``KEY=VALUE`` lines from a ``.env`` file into the process environment.

    Dependency-free (dd-trace-py ships no dotenv library). Writes through
    ``ddtrace.internal.settings.env`` (the repo-mandated accessor, which sets
    ``os.environ``) and **does not override** variables already set in the real
    environment — so exported values always win. Supports blank lines, ``#`` comments,
    an optional ``export`` prefix, and single/double-quoted values. Returns how many
    variables were set.
    """
    from ddtrace.internal.settings import env

    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return 0

    loaded = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]  # strip surrounding matching quotes
        if not key or key in env:  # skip empty keys and never override the real env
            continue
        env[key] = value
        loaded += 1
    return loaded


def _import_target(target: str) -> tuple[Any, Any]:
    """Resolve 'module' or 'module:callable'; importing registers decorated subjects."""
    mod_name, _, attr = target.partition(":")
    mod = importlib.import_module(mod_name)
    entry = getattr(mod, attr) if attr else None
    return mod, entry


def _enable_llmobs(ml_app_arg: Optional[str]) -> str:
    """Enable LLM Obs so the capture run's traces are viewable in the UI.

    Must run BEFORE importing the user's module so the LLM integrations are patched
    before the app constructs its clients. agentless is used when a DD API key is
    present; otherwise LLM Obs auto-detects a Datadog agent.
    """
    from ddtrace.internal.settings import env
    from ddtrace.llmobs import LLMObs

    ml_app = ml_app_arg or env.get("DD_LLMOBS_ML_APP") or "inline-experiments"
    if not LLMObs.enabled:
        agentless = True if env.get("DD_API_KEY") else None
        LLMObs.enable(ml_app=ml_app, agentless_enabled=agentless)
    return ml_app


def _flush_llmobs() -> None:
    try:
        from ddtrace.llmobs import LLMObs

        LLMObs.flush()
    except Exception:
        pass


def _trunc(v: Any, n: int = 34) -> str:
    import json

    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_report(name: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    # `exec` is the execution status (did we get an output to judge); the verdict is the
    # default comparison evaluator (`match`/`changed`) plus any attached evaluators.
    counts: dict[str, int] = {}
    print("\n  experiment: %s   (%d case(s))" % (name, len(rows)))
    print("  " + "-" * 92)
    print("  %-6s %-34s %-22s %-22s" % ("run", "input", "recorded", "new"))
    print("  " + "-" * 92)
    for r in rows:
        counts[r["exec"]] = counts.get(r["exec"], 0) + 1
        print(
            "  %-6s %-34s %-22s %-22s"
            % (r["exec"], _trunc(r["input"], 34), _trunc(r["recorded"], 22), _trunc(r["new"], 22))
        )
        for ev in r.get("evals", []):
            if ev["error"]:
                verdict = "error: %s" % ev["error"]
                counts["EVAL_ERROR"] = counts.get("EVAL_ERROR", 0) + 1  # the check didn't run -> gate CI
            else:
                verdict = ev["assessment"] if ev["assessment"] is not None else ev["value"]
                if ev["assessment"] == "changed":
                    counts["CHANGED"] = counts.get("CHANGED", 0) + 1
                elif ev["assessment"] == "fail":
                    counts["EVAL_FAIL"] = counts.get("EVAL_FAIL", 0) + 1
            print("      %-26s %s" % (_trunc(ev["name"], 26), _trunc(verdict, 44)))
    print("  " + "-" * 92)
    print("  " + "  ".join("%s=%d" % (k, v) for k, v in counts.items()) + "\n")
    return counts


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ddtrace-experiment", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Shared: load config from a .env file so DD_API_KEY / OPENAI_API_KEY / DD_LLMOBS_ML_APP
    # etc. don't have to be exported in the shell. Real env vars take precedence.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--env-file",
        default=None,
        help="load KEY=VALUE vars from this file first (default: .env in the current "
        "directory if present); real environment variables take precedence",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", parents=[common], help="run the entrypoint and persist a baseline")
    cap.add_argument("target", help="module:entrypoint that drives the app (e.g. myapp:generate_traffic)")
    cap.add_argument("--out", default=None, help="baseline file to write (default: .llmobs_experiments.json)")
    cap.add_argument(
        "--trace",
        action="store_true",
        help="also enable LLM Obs so the capture run's traces appear in the UI "
        "(needs DD_API_KEY or a Datadog agent); links each case to its trace",
    )
    cap.add_argument("--ml-app", default=None, help="ml_app for --trace/--publish (default: $DD_LLMOBS_ML_APP)")
    cap.add_argument(
        "--publish",
        action="store_true",
        help="publish the capture as the baseline experiment (real run -> real spans + cost) "
        "and a dataset in LLM Obs; requires the driver module to declare INPUTS (+ SUBJECT). "
        "Without it, capture stays local (JSON only): no dataset, no cost, no compare.",
    )
    cap.add_argument("--project", default=None, help="project name for --publish")
    cap.add_argument("--experiment-name", default=None, help="experiment name for --publish")

    rep = sub.add_parser("replay", parents=[common], help="replay the current code against a persisted baseline")
    rep.add_argument("target", help="module to import (registers the decorated subjects)")
    rep.add_argument(
        "--in", dest="infile", default=None, help="baseline file to read (default: .llmobs_experiments.json)"
    )
    rep.add_argument(
        "--comparator", default="structural", choices=["exact", "structural", "ignoring"], help="default: structural"
    )
    rep.add_argument("--ignore", default="", help="comma-separated keys to ignore (implies the 'ignoring' comparator)")
    rep.add_argument(
        "--publish",
        action="store_true",
        help="run via the LLM Obs Experiments SDK so results appear in the Experiments UI (needs DD_API_KEY)",
    )
    rep.add_argument("--ml-app", default=None, help="ml_app for --publish (default: $DD_LLMOBS_ML_APP)")
    rep.add_argument("--project", default=None, help="project name for --publish")
    rep.add_argument("--experiment-name", default=None, help="experiment name for --publish")
    rep.add_argument(
        "--evaluate",
        action="store_true",
        help="also score the boundary's attached `evaluators` locally and print verdicts "
        "(may call provider APIs for LLM judges); a failing evaluator gates the exit code. "
        "On --publish, evaluators always run through the Experiments engine.",
    )

    lst = sub.add_parser("list", parents=[common], help="import the target and list registered experiment subjects")
    lst.add_argument("target", help="module[:entrypoint] to import")
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Load a local .env first — before the prod fail-safe reads config and before the
    # app/LLM Obs are imported — so DD_*/OPENAI_API_KEY needn't be exported. Real env wins.
    env_file = args.env_file or ".env"
    if os.path.exists(env_file):
        loaded = _load_env_file(env_file)
        if loaded:
            print("ddtrace-experiment: loaded %d var(s) from %s" % (loaded, env_file))

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
        if args.publish:
            # Publish the capture AS the baseline experiment: run the real boundary through the
            # engine over the driver's declared INPUTS (one real run -> real spans + cost), and
            # a dataset. The module must declare INPUTS (+ SUBJECT); LLM Obs is enabled first.
            ml_app: Optional[str] = _enable_llmobs(args.ml_app)
            mod, _ = _import_target(args.target)  # registers subjects
            inputs = getattr(mod, "INPUTS", None)
            subject = getattr(mod, "SUBJECT", None)
            registered = ie.registered_experiments()
            if subject is None and len(registered) == 1:
                subject = registered[0]
            if not inputs or subject is None:
                print(
                    "capture --publish needs the driver module to declare INPUTS (a list of input "
                    "dicts, e.g. [{'tickers': ['NVDA']}]) and SUBJECT (the experiment name).",
                    file=sys.stderr,
                )
                sys.exit(2)
            from ddtrace.llmobs import _inline_experiment_sdk as sdk

            published = sdk.publish_baseline(
                subject, inputs, project_name=args.project, experiment_name=args.experiment_name
            )
            _flush_llmobs()
            out = args.out or runner.DEFAULT_BASELINE_PATH
            runner.save_baselines(out)  # persist the recorded (input, output) cases locally
            runner.save_publish_state(out, subject, baseline_experiment_id=sdk._experiment_id(published["baseline"]))
            print("published baseline for %r (ml_app: %s):" % (subject, ml_app))
            print("  baseline -> %s" % (sdk.experiment_url(published["baseline"]) or "?"))
            ds = sdk.dataset_url(published.get("dataset"))
            if ds:
                print("  dataset  -> %s" % ds)
            print("  (baseline recorded locally -> %s; change code, then `replay --publish`)" % os.path.abspath(out))
            return

        # Local capture (no --publish): run the driver, record cases, write JSON.
        # Enable LLM Obs BEFORE importing the app so its LLM integrations are patched
        # before the app builds its clients (otherwise the calls aren't traced).
        ml_app = _enable_llmobs(args.ml_app) if args.trace else None
        _, entry = _import_target(args.target)  # registers subjects
        if entry is None:
            print("capture needs 'module:entrypoint' to drive the app (e.g. myapp:generate_traffic).", file=sys.stderr)
            sys.exit(2)
        ie._set_trace(bool(args.trace))
        ie._set_mode(ie.Mode.CAPTURE)
        result = entry()
        if inspect.iscoroutine(result):
            asyncio.run(result)
        ie._set_mode(ie.Mode.OFF)
        ie._set_trace(False)
        if args.trace:
            _flush_llmobs()  # ensure the capture run's spans are sent before exit
        out = args.out or runner.DEFAULT_BASELINE_PATH
        data = runner.save_baselines(out)
        case_count = sum(len(v) for v in data.values())
        print("captured %d case(s) across %d experiment(s) -> %s" % (case_count, len(data), os.path.abspath(out)))
        if args.trace:
            linked = sum(1 for cases in data.values() for c in cases if c.get("trace"))
            print(
                "traces enabled (ml_app: %s) — %d/%d case(s) linked; view in LLM Observability."
                % (ml_app, linked, case_count)
            )
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

    if args.publish:
        # Run the CURRENT code as the `<name>` experiment over the shared dataset published at
        # capture, and link the compare view to the baseline experiment capture recorded.
        _enable_llmobs(args.ml_app)
        from ddtrace.llmobs import _inline_experiment_sdk as sdk

        for name, cases in baselines.items():
            if name not in ie.registered_experiments():
                print("  (skipping %r — not registered by %r)" % (name, args.target))
                continue
            published = sdk.publish_current(
                name, cases, comparator, experiment_name=args.experiment_name, project_name=args.project
            )
            state = runner.load_publish_state(infile, name) or {}
            baseline_id = state.get("baseline_experiment_id")
            print("published experiment %r:" % name)
            print("  current  -> %s" % (sdk.experiment_url(published["current"]) or "LLM Obs -> Experiments"))
            compare = sdk.compare_url_from_ids(baseline_id, sdk._experiment_id(published["current"]), args.project)
            if compare:
                print("  compare  -> %s" % compare)
            elif baseline_id is None:
                print("  (no baseline on record — run `capture --publish` first to get a compare view)")
            dataset = sdk.dataset_url(published.get("dataset"))
            if dataset:
                print("  dataset  -> %s" % dataset)
        return

    ie._set_mode(ie.Mode.REPLAY)
    total: dict[str, int] = {}
    for name, cases in baselines.items():
        if name not in ie.registered_experiments():
            print("  (skipping %r — not registered by %r)" % (name, args.target))
            continue
        counts = _print_report(name, runner.replay(name, comparator, cases=cases, score_evaluators=args.evaluate))
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
    ie._set_mode(ie.Mode.OFF)

    # CI-friendly: non-zero exit if the run errored or never reached its end (exec), the
    # default comparison evaluator reported `changed`, or (with --evaluate) an attached
    # evaluator failed OR errored (a check that didn't run isn't a pass).
    gate = ("CHANGED", "ERROR", "NO_END", "EVAL_FAIL", "EVAL_ERROR")
    sys.exit(1 if any(total.get(k) for k in gate) else 0)


if __name__ == "__main__":
    main()
