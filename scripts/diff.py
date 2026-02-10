from argparse import ArgumentParser
from itertools import product
import logging
import os
from os.path import isabs
from os.path import relpath
import sys
from typing import Dict
from typing import List
from typing import Optional
from typing import TextIO
from typing import Tuple

from austin.stats import Frame  # type: ignore[import]
from austin.stats import InvalidSample
from austin.stats import Metric
from austin.stats import MetricType
from austin.stats import Sample
from rich.console import Console  # type: ignore[import]
from rich.progress import track  # type: ignore[import]


FoldedStack = List[Frame]

CONSOLE = Console(file=sys.stderr)
CWD = os.getcwd()


if os.environ.get("DD_TRACE_DEBUG_ENABLE", False):
    logging.basicConfig(level=logging.DEBUG)
    LOGGER: Optional[logging.Logger] = logging.getLogger()
else:
    LOGGER = None


# -- MODEL --

CACHED_NORMALIZED_STACKS: Dict[int, FoldedStack] = {}
DEFAULT_THRESHOLD = 1e-3


def _normalized_stack(stack: FoldedStack) -> FoldedStack:
    try:
        return CACHED_NORMALIZED_STACKS[id(stack)]
    except KeyError:
        CACHED_NORMALIZED_STACKS[id(stack)] = [_ for _ in stack if "ddtrace" not in _.filename]
    return CACHED_NORMALIZED_STACKS[id(stack)]


def score_stacks(
    x: List[Tuple[FoldedStack, Metric]], y: List[Tuple[FoldedStack, Metric]]
) -> List[Tuple[Tuple[int, int], float]]:
    """O(n * log(n)), n = len(x) * len(y)."""

    def score_stack(a: Tuple[FoldedStack, Metric], b: Tuple[FoldedStack, Metric]) -> float:
        """Score two folded stacks (modulo frames contributed by ddtrace).

        For multiple matches, prioritise those with similar Metric.
        """
        fa, ma = a
        fb, mb = b
        na = _normalized_stack(fa)
        nb = _normalized_stack(fb)

        if na == nb:
            try:
                # Reward stacks that have exact endings
                bonus = na[-1] == fa[-1] and nb[-1] == fb[-1]
            except IndexError:
                bonus = 0
            return 0.5 - abs(ma - mb) / (ma + mb) + 0.5 * bonus
        return 0

    return sorted(
        [
            ((i, j), score_stack(a, b))
            for ((i, a), (j, b)) in track(
                product(enumerate(x), enumerate(y)),
                total=len(x) * len(y),
                description="Scoring stacks",
                console=CONSOLE,
                transient=True,
            )
        ],
        key=lambda x: x[1],
        reverse=True,
    )


def match_folded_stacks(
    x: List[Tuple[FoldedStack, Metric]],
    y: List[Tuple[FoldedStack, Metric]],
    scale: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[Tuple[int, int]]:
    """O(len(x) * len(y))."""
    ss = score_stacks(x, y)
    mx, my = set(), set()
    matches = []
    for (i, j), s in ss:
        if i in mx or j in my or s == 0:
            continue
        mx.add(i)
        my.add(j)

        if LOGGER:
            stack = ":".join([_.function + ":" + str(_.line) for _ in _normalized_stack(x[i][0])])
            LOGGER.debug("Match: %f (%r | %r)  %s", s, x[i][1], y[j][1], stack)
            LOGGER.debug("")

        matches.append((i, j))

    matched_x = set()
    matched_y = set()
    matched_stacks_plus = []
    matched_stacks_minus = []
    new_stacks = []
    old_stacks = []

    # Matched stacks
    for i, j in matches:
        matched_x.add(i)
        matched_y.add(j)
        delta = (x[i][1] - y[j][1]) / scale
        if abs(delta) < threshold:
            continue

        if delta > 0:
            matched_stacks_plus.append((x[i][0], delta))
        elif delta < 0:
            matched_stacks_minus.append((y[j][0], -delta))

    # New stacks
    for i in range(len(x)):
        if i in matched_x:
            continue
        f, m = x[i]
        delta = m / scale
        if delta < threshold:
            continue

        if LOGGER:
            stack = ":".join([_.function + ":" + str(_.line) for _ in _normalized_stack(f)])
            LOGGER.debug("NO Match (%r)  %s", m, stack)
            LOGGER.debug("")

        new_stacks.append((f, delta))

    # Old stacks
    for j in range(len(y)):
        if j in matched_y:
            continue
        f, m = y[j]
        delta = m / scale
        if delta < threshold:
            continue

        if LOGGER:
            stack = ":".join([_.function + ":" + str(_.line) for _ in _normalized_stack(f)])
            LOGGER.debug("NO Match (%r)  %s", m, stack)
            LOGGER.debug("")

        old_stacks.append((f, delta))

    return matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks


def compressed(source: TextIO) -> Tuple[str, int]:
    """Compress the source."""
    stats: Dict[str, int] = {}
    total_time = 0
    for line in track(
        source.read().splitlines(keepends=False),
        description="Compressing",
        console=CONSOLE,
        transient=True,
    ):
        if line.startswith("# "):
            continue
        stack, _, metric = line.rpartition(" ")
        stack = stack.replace(CWD, ".")
        try:
            v = int(metric)
        except ValueError:
            continue
        stats[stack] = stats.setdefault(stack, 0) + v
        total_time += v

    return "\n".join([stack + " " + str(t) for stack, t in stats.items()]), total_time


def get_folded_stacks(text: str, threshold: float = DEFAULT_THRESHOLD) -> List[Tuple[FoldedStack, Metric]]:
    """Get the folded stacks and Metric from a string of samples."""
    x = []
    max_time = 1
    for _ in track(
        text.splitlines(keepends=False),
        description="Extracting frames",
        console=CONSOLE,
        transient=True,
    ):
        try:
            (sample,) = Sample.parse(_, MetricType.TIME)
            if sample.metric.value > max_time:
                max_time = sample.metric.value
            x.append((sample.frames, sample.metric.value))
        except InvalidSample:
            continue
    return [_ for _ in x if _[1] / max_time > threshold]


def top(stacks):
    top_map = {}

    def _k(f):
        return "%s (%s:%d)" % (f.function, f.filename, f.line)

    for fs, delta in stacks:
        seen_fs = set()
        for f in fs:
            key = _k(f)
            if key in seen_fs:
                continue
            top_map.setdefault(key, {"own": 0, "total": 0})["total"] += delta
            seen_fs.add(key)
        if fs:
            top_map[key]["own"] += delta

    return sorted(
        ((k, v) for k, v in top_map.items()),
        key=lambda e: e[1]["own"],
        reverse=True,
    )


# -- VIEW --


def make_top(stacks, overhead, output):
    output.write("Total overhead: %0.2f%%\n\n" % (overhead * 100))
    output.write("     %TOT   * %OWN  FUNCTION\n")
    output.write(
        "\n".join(
            [
                "{} {:6.2f}%  {:6.2f}%  {}".format("*" if "ddtrace" in f else " ", v["total"] * 100, v["own"] * 100, f)
                for f, v in top(stacks)
            ]
        )
    )


def make_stacks(matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks, stacks_stream):
    def repr_frame(frame: Frame) -> str:
        filename = frame.filename
        if isabs(filename):
            filename = relpath(filename)
        return "%s:%s:%d" % (filename, frame.function, frame.line)

    M = 1e8

    def join_stacks(stacks, prefix):
        return "\n".join(
            [
                ";".join(["P0", "T" + prefix] + [repr_frame(f) for f in fs]) + " %d" % int(delta * M)
                for fs, delta in stacks
            ]
        )

    stacks_stream.write(
        "\n".join(
            [
                join_stacks(s, p)
                for s, p in [
                    (matched_stacks_plus, "-GROWN"),
                    (new_stacks, "-APPEARED"),
                    (matched_stacks_minus, "-SHRUNK"),
                    (old_stacks, "-DISAPPEARED"),
                ]
            ]
        )
    )


# -- CONTROLLER --


def diff(a: str, b: str, output_prefix: str, threshold: float = 1e-3) -> None:
    """Compare stacks and return a - b.

    The algorithm attempts to match stacks that look similar and returns
    only positive time differences (that is, stacks of b that took longer
    than those in a are not reported), plus any new stacks that are not in
    b.
    """
    with open(a) as a_in, open(b) as b_in:
        ca, ta = compressed(a_in)
        cb, tb = compressed(b_in)

    with open(a, "w") as aout, open(b, "w") as bout:
        # overwrite files with compressed result
        aout.write(ca)
        bout.write(cb)

    overhead = (ta - tb) / tb

    fa = get_folded_stacks(ca, threshold)
    fb = get_folded_stacks(cb, threshold)
    matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks = match_folded_stacks(fa, fb, tb, threshold)

    with open(output_prefix + ".austin", "w") as stacks_stream:
        make_stacks(matched_stacks_plus, matched_stacks_minus, new_stacks, old_stacks, stacks_stream)
    with open(output_prefix + ".top", "w") as top_stream:
        make_top(matched_stacks_plus + new_stacks, overhead, top_stream)


def main() -> None:
    """Diff tool for Austin samples."""
    argp = ArgumentParser(
        prog="austin-diff",
        description=("Compute the diff between two austin frame stack sample files"),
    )

    argp.add_argument(
        "a",
        type=str,
        help="The minuend collapsed stacks",
    )
    argp.add_argument(
        "b",
        type=str,
        help="The subtrahend collapsed stacks",
    )
    argp.add_argument(
        "output_prefix",
        type=str,
        help="The output file prefix",
    )
    argp.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=1e-3,
        help="Relative stack width threshold. Increase for speedups",
    )
    argp.add_argument(
        "-V",
        "--version",
        action="version",
        version="1.0.0",
    )

    args = argp.parse_args()

    diff(args.a, args.b, args.output_prefix, args.threshold)


if __name__ == "__main__":
    main()
