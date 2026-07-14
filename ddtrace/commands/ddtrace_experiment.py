#!/usr/bin/env python
"""``ddtrace-experiment`` — run inline experiments (trace-seeded local regression).

The explicit, out-of-band trigger for the ``@experiment_start`` / ``@experiment_end``
decorators. One verb, ``run``, drives the whole loop and auto-detects the phase from
whether a baseline already exists:

    # 1. first run — establish a baseline from the current (known-good) code
    ddtrace-experiment run myapp:generate_traffic
    # 2. ...edit your prompt/model/logic...
    # 3. run again — compare the current code against the baseline
    ddtrace-experiment run myapp --comparator structural

``run`` is **offline by default**: it records or compares against a local baseline file
(``.llmobs_experiments.json``) and sets a CI-friendly exit code, with no backend or
credentials required. Add ``--publish`` (opt-in) to also send the run to LLM Obs
Experiments: the first publish is the frozen **baseline** (a real run with cost + eval
metrics over the subject's auto-managed dataset); every later publish is compared against
it in the **compare view**. The dataset is refreshed to exactly each run's inputs.

Publish conventions (module-level, optional): ``--publish`` operates on one subject over a
set of inputs, resolved from the imported module:
  * subject — ``--name``, else a module global ``SUBJECT``, else the sole registered subject
    (required if several are registered).
  * inputs — a module global ``INPUTS`` (a list; what you're testing now), else the inputs
    from a prior offline baseline. ``INPUTS`` wins, so editing the module drives the refresh.

Activation is positive and explicit — the decorators are inert unless this command runs,
so they are safe to leave in production code. As a one-way fail-safe this command also
refuses to run when it looks like production (no TTY and ``DD_ENV=prod``).
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
        try:
            LLMObs.enable(ml_app=ml_app, agentless_enabled=agentless)
        except Exception as e:  # surface an actionable message instead of a raw traceback
            print(
                "run --publish: could not enable LLM Observability (%s). Check DD_API_KEY / "
                "DD_SITE (agentless) or that a Datadog agent is reachable, then re-run." % e,
                file=sys.stderr,
            )
            sys.exit(2)
    return ml_app


def _flush_llmobs() -> None:
    try:
        from ddtrace.llmobs import LLMObs

        LLMObs.flush()
    except Exception:  # nosec B110 - best-effort flush on exit; must never fail the CLI
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


def _run_record_offline(entry: Any, out: str, runner: Any, ie: Any) -> None:
    """First run: drive the app in CAPTURE mode and persist the baseline (offline)."""
    if entry is None:
        print(
            "run: no baseline yet, and no driver to establish one — pass a driver as "
            "'module:entrypoint' (e.g. myapp:generate_traffic) for the first run.",
            file=sys.stderr,
        )
        sys.exit(2)
    ie._set_mode(ie.Mode.CAPTURE)
    try:
        result = entry()
        if inspect.iscoroutine(result):
            asyncio.run(result)
    finally:
        ie._set_mode(ie.Mode.OFF)
    data = runner.save_baselines(out)
    case_count = sum(len(v) for v in data.values())
    print("recorded baseline: %d case(s) across %d subject(s) -> %s" % (case_count, len(data), os.path.abspath(out)))
    print("edit your code, then `ddtrace-experiment run <module>` to compare against it.")


def _run_compare_offline(args: Any, ie: Any, runner: Any, baseline_file: str) -> None:
    """Rerun: replay the current code over the recorded baseline and report (offline).

    Exits non-zero (CI gate) if any case errored, never reached its end, the default
    comparison reported ``changed``, or (with ``--evaluate``) an attached evaluator
    failed/errored.
    """
    baselines = runner.load_baselines(baseline_file)
    ignore = [k for k in args.ignore.split(",") if k]
    comparator = runner.comparator_from_spec(args.comparator, ignore)
    ie._set_mode(ie.Mode.REPLAY)
    total: dict[str, int] = {}
    report: dict[str, Any] = {}
    compared = False
    try:
        for name, cases in runner.subject_items(baselines):  # skips the reserved `_publish` block
            if args.name and name != args.name:
                continue
            if name not in ie.registered_experiments():
                print("  (skipping %r — not registered by %r)" % (name, args.target))
                continue
            compared = True
            rows = runner.replay(name, comparator, cases=cases, score_evaluators=args.evaluate)
            counts = _print_report(name, rows)
            report[name] = {"counts": counts, "cases": rows}  # full, untruncated
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
    finally:
        ie._set_mode(ie.Mode.OFF)
    if not compared:
        which = "subject %r" % args.name if args.name else "any registered subject"
        print("run: no baseline cases for %s in %s." % (which, baseline_file), file=sys.stderr)
        sys.exit(2)
    if args.report:
        import json

        with open(args.report, "w") as f:
            json.dump(report, f, default=str, indent=2)
        print("full report (untruncated input/recorded/new + evals) -> %s" % os.path.abspath(args.report))
    gate = ("CHANGED", "ERROR", "NO_END", "EVAL_FAIL", "EVAL_ERROR")
    sys.exit(1 if any(total.get(k) for k in gate) else 0)


def _resolve_subject(args: Any, ie: Any, mod: Any) -> str:
    """The single subject a publish run operates on: --name, then module SUBJECT, then the
    sole registered subject. Exits(2) if ambiguous or unregistered.
    """
    registered = ie.registered_experiments()
    subject = args.name or getattr(mod, "SUBJECT", None) or (registered[0] if len(registered) == 1 else None)
    if subject is None:
        print("run --publish: several subjects registered; pass --name to choose: %s" % ", ".join(registered))
        sys.exit(2)
    if subject not in registered:
        print("run --publish: subject %r is not registered by %r." % (subject, args.target), file=sys.stderr)
        sys.exit(2)
    return str(subject)


def _publish_inputs(subject: str, mod: Any, baseline_file: str, runner: Any) -> list[Any]:
    """This run's inputs: the module's current ``INPUTS`` (what the user is testing now), else
    a prior offline baseline's inputs. INPUTS wins so editing the module drives the refresh.
    """
    module_inputs = getattr(mod, "INPUTS", None)
    if module_inputs:
        return list(module_inputs)
    if os.path.exists(baseline_file):
        try:
            cases = runner.load_baselines(baseline_file).get(subject) or []
            return [c["input"] for c in cases if isinstance(c, dict) and "input" in c]
        except Exception:  # nosec B110 - best-effort read of a prior baseline; fall through to []
            pass
    return []


def _cmd_run_publish(args: Any, ie: Any, runner: Any, baseline_file: str) -> None:
    """`run --publish`: first publish creates the dataset + baseline experiment; a later publish
    adds the current experiment + compare view. Correlation is the ``_publish`` block embedded
    in the baseline file (no sidecar).
    """
    # Enable LLM Obs BEFORE importing the app so its LLM integrations are patched first.
    _enable_llmobs(args.ml_app)
    mod, _ = _import_target(args.target)
    if not ie.registered_experiments():
        print("run --publish: no experiment subjects registered by %r." % args.target, file=sys.stderr)
        sys.exit(2)
    subject = _resolve_subject(args, ie, mod)

    from ddtrace.llmobs import _inline_experiment_sdk as sdk

    inputs = _publish_inputs(subject, mod, baseline_file, runner)
    if not inputs:
        print(
            "run --publish: no inputs for %r — define INPUTS in the module or run offline "
            "`run %s` first to capture some." % (subject, args.target),
            file=sys.stderr,
        )
        sys.exit(2)

    meta = None if args.record else runner.load_publish_meta(baseline_file, subject)
    baseline_id = meta.get("baseline_experiment_id") if meta else None

    # Every run is the same operation: sync the stable dataset to this run's inputs and run the
    # subject as one experiment (scored by the subject's own evaluators). The regression signal
    # is the compare view, not an in-experiment check.
    try:
        published = sdk.publish_run(subject, inputs, project_name=args.project, experiment_name=args.experiment_name)
    except sdk.PublishAbort as e:
        print("run --publish: %s" % e, file=sys.stderr)
        sys.exit(1)
    sync = published.get("sync") or {}
    print(
        "published experiment %r over dataset %r  (+%d/-%d, %d kept)"
        % (
            subject,
            published["dataset_name"],
            sync.get("added", 0),
            sync.get("deleted", 0),
            sync.get("kept", 0),
        )
    )
    # Always surface both URLs once they exist.
    print("  experiment -> %s" % (published.get("url") or "LLM Obs -> Experiments"))
    print("  dataset    -> %s" % (published.get("dataset_url") or "LLM Obs -> Datasets"))

    if baseline_id and not sdk.experiment_exists(baseline_id):
        # The frozen baseline experiment is gone (deleted in the UI) -> a compare link would
        # 404. Tell the user to re-establish it instead of printing a dead link.
        print(
            "  (frozen baseline experiment %s no longer exists — re-record it with "
            "`ddtrace-experiment run %s --publish --record`)" % (baseline_id, args.target)
        )
    elif baseline_id:
        # A frozen baseline exists -> link the compare view (baseline vs this run).
        compare = sdk.compare_url_from_ids(baseline_id, published["experiment_id"], args.project)
        if compare:
            print("  compare    -> %s" % compare)
    else:
        # First publish: this run becomes the frozen baseline; also seed the local baseline so
        # offline `run` has a reference. Nothing to compare against yet.
        runner.write_baseline_cases(baseline_file, subject, published["pairs"])
        runner.save_publish_meta(
            baseline_file,
            subject,
            dataset_name=published["dataset_name"],
            baseline_experiment_id=published["experiment_id"],
            project=args.project or "",
        )
        print(
            "  (this run is the frozen baseline — run `ddtrace-experiment run %s --publish` again "
            "after a change to get a compare view)" % args.target
        )
    _flush_llmobs()


def _cmd_run(args: Any, ie: Any, runner: Any) -> None:
    """The unified ``run`` verb: offline by default; ``--publish`` routes to the backend."""
    baseline_file = args.baseline_file or runner.DEFAULT_BASELINE_PATH
    if args.publish:
        _cmd_run_publish(args, ie, runner, baseline_file)
        return

    _, entry = _import_target(args.target)  # registers subjects
    if not ie.registered_experiments():
        print("run: no experiment subjects registered by %r (did you import the app module?)." % args.target)
        sys.exit(2)
    have_baseline = os.path.exists(baseline_file)
    # First run when there is nothing to compare against, or when explicitly forced.
    if args.record or not have_baseline:
        reason = "--record: re-recording baseline" if have_baseline else "no baseline yet — first run"
        print("run: %s -> %s" % (reason, baseline_file))
        _run_record_offline(entry, baseline_file, runner, ie)
        return
    print("run: baseline found (%s) — comparing current code (use --record to re-establish)." % baseline_file)
    _run_compare_offline(args, ie, runner, baseline_file)


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

    run = sub.add_parser(
        "run",
        parents=[common],
        help="run the subject as an experiment; offline by default, auto-detecting first-run vs compare",
    )
    run.add_argument(
        "target",
        help="module to compare against the baseline, OR a driver 'module:entrypoint' to establish one",
    )
    run.add_argument("--name", default=None, help="operate on just this subject (default: all registered)")
    run.add_argument(
        "--record", action="store_true", help="force a first run: (re)record the baseline even if one exists"
    )
    run.add_argument(
        "--baseline-file",
        dest="baseline_file",
        default=None,
        help="local baseline JSON to read/write (default: .llmobs_experiments.json)",
    )
    run.add_argument(
        "--comparator", default="structural", choices=["exact", "structural", "ignoring"], help="default: structural"
    )
    run.add_argument("--ignore", default="", help="comma-separated keys to ignore (implies the 'ignoring' comparator)")
    run.add_argument(
        "--report",
        nargs="?",
        const=".llmobs_experiments.report.json",
        default=None,
        help="write the FULL (untruncated) offline comparison — per-case input / recorded / new + all "
        "evaluator verdicts and reasoning — to a JSON file (default: .llmobs_experiments.report.json). "
        "The terminal report stays a compact summary.",
    )
    run.add_argument(
        "--evaluate",
        action="store_true",
        help="also score the boundary's attached `evaluators` locally and print verdicts "
        "(may call provider APIs for LLM judges); a failing evaluator gates the exit code",
    )
    run.add_argument(
        "--publish",
        action="store_true",
        help="send the run to LLM Obs Experiments (needs DD_API_KEY): the first publish creates a "
        "dataset + baseline experiment (with cost); a later publish adds the current experiment + a "
        "compare view. Offline behavior is unchanged when omitted.",
    )
    run.add_argument("--project", default=None, help="project name for --publish")
    run.add_argument("--experiment-name", default=None, help="experiment name for --publish (default: the subject)")
    run.add_argument("--ml-app", default=None, help="ml_app for --publish (default: $DD_LLMOBS_ML_APP)")

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

    if args.command == "run":
        _cmd_run(args, ie, runner)
        return


if __name__ == "__main__":
    main()
