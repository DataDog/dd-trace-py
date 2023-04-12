import contextlib
from itertools import count
from itertools import groupby
import json
import os
from typing import Iterable
from typing import List
from typing import Tuple

from coverage import Coverage
from coverage import version_info as coverage_version
from coverage.numbits import numbits_to_nums

from ddtrace import config
from ddtrace.internal import compat
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import find_loader

from .constants import COVERAGE_TAG_NAME


log = get_logger(__name__)

# this public attribute became private after coverage==6.3
EXECUTE_ATTR = "_execute" if coverage_version > (6, 3) else "execute"


def enabled():
    if compat.PY2:
        return False
    if config._ci_visibility_code_coverage_enabled:
        if find_loader("coverage") is None:
            log.warning(
                "CI Visibility code coverage tracking is enabled, but the `coverage` package is not installed. "
                "To use code coverage tracking, please install `coverage` from https://pypi.org/project/coverage/"
            )
            return False
        return True
    return False


@contextlib.contextmanager
def cover(span, root=None, **kwargs):
    coverage_kwargs = {
        "data_file": None,
        "source": [root] if root else None,
        "config_file": False,
    }
    coverage_kwargs.update(kwargs)
    cov = Coverage(**coverage_kwargs)
    cov.start()
    cov.switch_context(str(span.trace_id))
    yield cov
    cov.stop()
    span.set_tag(COVERAGE_TAG_NAME, build_payload(cov, test_id=str(span.trace_id), root=root))


def segments(lines):
    # type: (Iterable[int]) -> Iterable[Tuple[int, int, int, int, int]]
    """Extract the relevant report data for a single file."""

    def as_segments(it):
        # type: (Iterable[int]) -> Tuple[int, int, int, int, int]
        sequence = list(it)  # type: List[int]
        return (sequence[0], 0, sequence[-1], 0, -1)

    def grouper(n, c=count()):
        # type: (int, count[int]) -> int
        return n - next(c)

    executed = sorted(lines)
    return [as_segments(g) for _, g in groupby(executed, grouper)]


def _lines(coverage, context):
    data = coverage.get_data()
    context_id = data._context_id(context)
    data._start_using()
    with data._connect() as con:
        query = (
            "select file.path, line_bits.numbits "
            "from line_bits "
            "join file on line_bits.file_id = file.id "
            "where context_id = ?"
        )
        data = [context_id]
        bitmaps = list(getattr(con, EXECUTE_ATTR)(query, data))
        return {row[0]: numbits_to_nums(row[1]) for row in bitmaps if not row[0].startswith("..")}


def build_payload(coverage, test_id=None, root=None):
    """Generate a CI Visibility payload."""
    return json.dumps(
        {
            "files": [
                {
                    "filename": os.path.relpath(filename, root) if root is not None else filename,
                    "segments": segments(lines),
                }
                for filename, lines in _lines(coverage, test_id).items()
            ]
        }
    )
