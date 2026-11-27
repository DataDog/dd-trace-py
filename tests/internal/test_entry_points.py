import importlib.metadata as importlib_metadata
import sys

import pytest

from ddtrace.internal.utils import entry_points
from ddtrace.internal.utils.entry_points import distribution_name
from ddtrace.internal.utils.entry_points import entry_point_module_path
from ddtrace.internal.utils.entry_points import entry_points_with_distributions
from ddtrace.internal.utils.entry_points import is_trusted_entry_point


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


class _FakeDist:
    def __init__(self, name):
        self.metadata = {"Name": name}


class _FakeEntryPoint:
    def __init__(self, name, value):
        self.name = name
        self.group = "ddtrace.products"
        self.value = value


@pytest.mark.parametrize("value, expected", [("mod", "mod"), ("mod:obj", "mod"), ("mod:pkg.obj", "mod")])
def test_entry_point_module_path_strips_the_object(value, expected):
    assert entry_point_module_path(_FakeEntryPoint("ep", value)) == expected


@pytest.mark.parametrize(
    "dist_name, value, expected",
    [
        # ddtrace's own distributions resolving into its module namespaces
        ("ddtrace", "ddtrace.internal.settings._supported_configurations", True),
        ("ddtrace", "ddtrace.internal.settings._supported_configurations:REGISTRY", True),
        ("DDTrace", "ddtrace._product", True),  # dist names are normalized
        ("ddtrace-internal", "_ddtrace_internal.settings._supported_configurations", True),
        ("ddtrace-serverless", "ddtrace._product", True),
        # untrusted: another distribution, or a trusted name pointing outside the namespaces
        ("evil", "ddtrace.internal.settings._supported_configurations", False),
        ("ddtrace", "evil_module._supported_configurations", False),
        ("evil", "evil_module._registry", False),
        (None, "ddtrace.internal.settings._supported_configurations", False),  # malformed metadata
    ],
)
def test_is_trusted_entry_point(dist_name, value, expected):
    trusted_dists = frozenset({"ddtrace", "ddtrace-serverless", "ddtrace-internal"})
    trusted_prefixes = frozenset({"ddtrace.", "_ddtrace_internal."})

    entry_point = _FakeEntryPoint("ep", value)
    result = is_trusted_entry_point(entry_point, _FakeDist(dist_name), trusted_dists, trusted_prefixes)

    assert result is expected


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
