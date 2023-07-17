from os.path import abspath
from os.path import join
from os.path import relpath
from os.path import sep

from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import _resolve_source_file
from tests.debugging.utils import create_log_line_probe


def test_resolve_source_file():
    rpath = relpath(__file__)
    path = join("some", "prefix", rpath)

    # Test that we can handle arbitrary prefixes
    assert _resolve_source_file(path) == abspath(__file__)

    # Test that we fail if we have incomplete source paths
    _, _, child = rpath.partition(sep)
    assert _resolve_source_file(child) is None


def test_mutability():
    before = create_log_line_probe(
        probe_id="test_mutability",
        version=1,
        condition=None,
        source_file="foo",
        line=1,
        template="",
        segments=[],
    )
    after = create_log_line_probe(
        probe_id="test_mutability",
        version=2,
        condition=DDExpression(dsl="True", callable=dd_compile(True)),
        source_file="foo",
        line=1,
        template="",
        segments=[],
    )

    assert before != after

    before.update(after)

    assert before == after
