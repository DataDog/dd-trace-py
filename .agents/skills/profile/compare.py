"""Compare Austin profiles of the same workload, e.g. before and after a change.

Usage:
  python compare.py --baseline A-*.mojo --candidate B-*.mojo [-k K] [--by own|total]
                    [-m METRIC] [--focus SELECTOR] [--thread NAME] [--no-idle] [--ddtrace-only] [--json]

Each profile is projected onto a small set of functions (the K heaviest across
all runs), and the two sets of runs are compared with pyrometry's two-sample
Hotelling T^2 test. The functions whose change is significant are reported as
appeared, disappeared, grown or shrunk. Sampling noise is large compared to
most code changes, so a single run per side cannot tell the two apart; with one
run per side the script only prints a descriptive diff.

Values are absolute (time or memory), so all runs must do the same amount of
work. The total and the share of the profile with ddtrace on the stack are
compared with the same test on a single value, which is Student's t-test.
"""

from argparse import ArgumentParser
import json
import math
import sys
import typing as t

from austin.query import Location
from austin.query import ProfileQuery
import numpy as np
from pyrometry.analysis import compare
from pyrometry.analysis import decompose_4way
from pyrometry.flamegraph import FlameGraph
from pyrometry.stats import hotelling_two_sample_test


DEFAULT_K = 10


def is_ddtrace(frame: t.Any) -> bool:
    return "/ddtrace/" in frame.filename


class Run:
    """The projection of one profile: function values, total and ddtrace share."""

    def __init__(self, path: str, args: t.Any):
        metric, by = args.metric, args.by
        query = ProfileQuery.from_file(path).filter(
            contains=args.focus, thread=args.thread, idle=False if args.no_idle else None
        )
        result = query.top(None, metric=metric, by=by)
        self.path = path
        self.total = result.profile_total
        self.fmt = result.fmt
        self.values: dict[Location, int] = {}
        self.labels: dict[Location, str] = {}
        for entry in result:
            if args.ddtrace_only and "/ddtrace/" not in entry.location.filename:
                continue
            self.values[entry.location] = getattr(entry, by)
            self.labels[entry.location] = result.label(entry.location)
        with_ddtrace = query.filter(contains=is_ddtrace).top(0, metric=metric).profile_total
        self.ddtrace_pct = 100 * with_ddtrace / self.total if self.total else 0.0


def mean(values: t.Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values)


def p_value(base: list[float], cand: list[float]) -> t.Optional[float]:
    """Return the p-value of the difference of a single value per run, if it can be tested."""
    if len(base) < 2 or len(cand) < 2:
        return None
    try:
        _, _, p, _ = hotelling_two_sample_test(
            np.array(cand, dtype=float)[:, None], np.array(base, dtype=float)[:, None]
        )
    except np.linalg.LinAlgError:  # No variance on either side
        return None
    return None if math.isnan(p) else float(p)


def main() -> None:
    argp = ArgumentParser(description="Compare Austin profiles of the same workload")
    argp.add_argument("--baseline", nargs="+", required=True, help="Baseline profiles, one per run")
    argp.add_argument("--candidate", nargs="+", required=True, help="Candidate profiles, one per run")
    argp.add_argument("-k", type=int, default=DEFAULT_K, help="Number of functions to compare")
    argp.add_argument("--by", choices=("own", "total"), default="own")
    argp.add_argument("-m", "--metric", help="wall, cpu, memory or dealloc")
    argp.add_argument("--focus", help="Only keep samples with a frame matching this selector")
    argp.add_argument("--thread", help="Only keep the samples of this thread")
    argp.add_argument("--no-idle", action="store_true", help="Drop idle samples")
    argp.add_argument("--ddtrace-only", action="store_true", help="Only compare ddtrace functions")
    argp.add_argument("--json", action="store_true")
    args = argp.parse_args()

    def load(paths: list[str]) -> list[Run]:
        return [Run(p, args) for p in paths]

    base, cand = load(args.baseline), load(args.candidate)
    runs = base + cand
    fmt = runs[0].fmt
    labels: dict[Location, str] = {}
    for run in runs:
        labels.update(run.labels)

    # The test needs more runs than compared functions (nx + ny - p - 1 > 0).
    k = min(args.k, len(runs) - 2)
    weight: dict[Location, int] = {}
    for run in runs:
        for location, value in run.values.items():
            weight[location] = weight.get(location, 0) + value
    domain = sorted(weight, key=weight.__getitem__, reverse=True)[: max(k, 0)]

    report: dict[str, t.Any] = {
        "runs": {"baseline": len(base), "candidate": len(cand)},
        "total": {
            "baseline": mean(r.total for r in base),
            "candidate": mean(r.total for r in cand),
            "p_value": p_value([r.total for r in base], [r.total for r in cand]),
        },
        "ddtrace_pct": {
            "baseline": mean(r.ddtrace_pct for r in base),
            "candidate": mean(r.ddtrace_pct for r in cand),
            "p_value": p_value([r.ddtrace_pct for r in base], [r.ddtrace_pct for r in cand]),
        },
        "test": None,
        "functions": [],
    }

    significant: dict[str, str] = {}
    if k >= 1:

        def to_fg(run: Run) -> FlameGraph:
            return FlameGraph({str(loc): run.values[loc] for loc in domain if run.values.get(loc)})

        x, y = [to_fg(r) for r in cand], [to_fg(r) for r in base]
        try:
            _, f_stat, test_p = compare(x, y)
            parts = decompose_4way(x, y)
        except np.linalg.LinAlgError:
            print("warning: singular covariance; lower -k or collect more runs", file=sys.stderr)
        else:
            report["test"] = {"functions": len(domain), "f": float(f_stat), "p_value": float(test_p)}
            for kind, part in zip(("appeared", "disappeared", "grown", "shrunk"), parts):
                for key in part:
                    significant[key] = kind
    else:
        print("warning: one run per side, no significance test; use --runs to collect more", file=sys.stderr)
        domain = sorted(weight, key=weight.__getitem__, reverse=True)[: args.k]

    for location in domain:
        b = mean(r.values.get(location, 0) for r in base)
        c = mean(r.values.get(location, 0) for r in cand)
        report["functions"].append(
            {
                "location": str(location),
                "label": labels[location],
                "baseline": b,
                "candidate": c,
                "delta": c - b,
                "change": significant.get(str(location)),
            }
        )
    report["functions"].sort(key=lambda f: abs(f["delta"]), reverse=True)

    if args.json:
        print(json.dumps(report))
        return

    def p(value: t.Optional[float]) -> str:
        return "n/a" if value is None else f"{value:.3g}"

    total, share, test = report["total"], report["ddtrace_pct"], report["test"]
    print(f"runs:    {len(base)} baseline, {len(cand)} candidate")
    print(f"total:   {fmt(int(total['baseline'])):>10} -> {fmt(int(total['candidate'])):>10}  p={p(total['p_value'])}")
    print(f"ddtrace: {share['baseline']:9.1f}% -> {share['candidate']:9.1f}%  p={p(share['p_value'])}")
    if test:
        print(f"Hotelling T^2 over {test['functions']} functions: F={test['f']:.3g} p={p(test['p_value'])}")
    print()
    print(f"{'baseline':>10} {'candidate':>10} {'delta':>10}  {'change':<11} {args.by} location")
    for f in report["functions"]:
        delta = fmt(int(abs(f["delta"])))
        sign = "-" if f["delta"] < 0 else "+"
        print(
            f"{fmt(int(f['baseline'])):>10} {fmt(int(f['candidate'])):>10} {sign + delta:>10}  "
            f"{f['change'] or '':<11} {f['label']}"
        )


if __name__ == "__main__":
    main()
