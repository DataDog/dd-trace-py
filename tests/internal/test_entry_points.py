import importlib.metadata as importlib_metadata
import sys

import pytest

from ddtrace.internal.utils import entry_points
from ddtrace.internal.utils.entry_points import distribution_name
from ddtrace.internal.utils.entry_points import entry_points_with_distributions


GROUP = "ddtrace.products"


@pytest.fixture(params=[True, False], ids=["with-dist", "walk-distributions"])
def lookup_path(request, monkeypatch):
    """Exercise both lookup paths, whatever the running Python version."""
    if request.param and sys.version_info < (3, 10):
        pytest.skip("EntryPoint.dist needs Python 3.10")
    monkeypatch.setattr(entry_points, "_ENTRY_POINT_HAS_DIST", request.param)


@pytest.mark.parametrize(
    "raw, expected",
    [("ddtrace", "ddtrace"), ("DDTrace_Internal", "ddtrace-internal"), ("dd.trace--x", "dd-trace-x")],
)
def test_distribution_name_is_normalized(raw, expected):
    class _Dist:
        metadata = {"Name": raw}

    assert distribution_name(_Dist()) == expected


def test_distribution_name_without_name():
    class _Dist:
        metadata = {"Name": None}

    assert distribution_name(_Dist()) == ""


def test_entry_points_come_with_their_declaring_distribution(lookup_path):
    found = entry_points_with_distributions(GROUP)

    assert found, "ddtrace declares products"
    assert {ep.name for ep, _ in found} >= {"tracer"}
    assert {distribution_name(dist) for _, dist in found} == {"ddtrace"}
    assert all(ep.group == GROUP for ep, _ in found)


def test_both_paths_find_the_same_entry_points(monkeypatch):
    if sys.version_info < (3, 10):
        pytest.skip("EntryPoint.dist needs Python 3.10")
    monkeypatch.setattr(entry_points, "_ENTRY_POINT_HAS_DIST", True)
    with_dist = sorted(ep.name for ep, _ in entry_points_with_distributions(GROUP))
    monkeypatch.setattr(entry_points, "_ENTRY_POINT_HAS_DIST", False)
    walked = sorted(ep.name for ep, _ in entry_points_with_distributions(GROUP))

    assert with_dist == walked


def test_unknown_group_is_empty(lookup_path):
    assert entry_points_with_distributions("ddtrace.no_such_group") == []


def test_a_distribution_installed_twice_counts_once(monkeypatch):
    monkeypatch.setattr(entry_points, "_ENTRY_POINT_HAS_DIST", False)
    real = list(importlib_metadata.distributions())
    monkeypatch.setattr(importlib_metadata, "distributions", lambda: real + real)

    names = [ep.name for ep, _ in entry_points_with_distributions(GROUP)]

    assert len(names) == len(set(names))
