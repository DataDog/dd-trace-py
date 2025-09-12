from pathlib import Path

from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import _resolve_source_file
from tests.debugging.utils import create_log_line_probe


def test_resolve_source_file():
    rpath = Path(__file__).relative_to(Path.cwd())
    path = Path("some") / "prefix" / rpath

    # Test that we can handle arbitrary prefixes
    assert _resolve_source_file(path) == Path(__file__).resolve()

    # Test that we fail if we have incomplete source paths
    child = rpath.relative_to(rpath.parent)
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


def test_probe_hash():
    probe = create_log_line_probe(
        probe_id="test_mutability",
        version=2,
        condition=DDExpression(dsl="True", callable=dd_compile(True)),
        source_file="foo",
        line=1,
        template="",
        segments=[],
    )

    assert hash(probe)
