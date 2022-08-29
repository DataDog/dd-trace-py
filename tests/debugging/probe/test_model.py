from os.path import abspath
from os.path import join
from os.path import relpath
from os.path import sep

from ddtrace.debugging._probe.model import _resolve_source_file


def test_resolve_source_file():
    rpath = relpath(__file__)
    path = join("some", "prefix", rpath)

    # Test that we can handle arbitrary prefixes
    assert _resolve_source_file(path) == abspath(__file__)

    # Test that we fail if we have incomplete source paths
    _, _, child = rpath.partition(sep)
    assert _resolve_source_file(child) is None
