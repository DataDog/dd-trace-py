"""Resilience tests for ``ddtrace.internal.packages``.

Real-world environments (Linux distro Python, ``pip install -e`` from old
pip versions, conda-pip mixes, CI base images) ship dist-info directories
with malformed or unreadable METADATA. Before the fix, a single bad dist
made ``get_distributions`` raise; ``@callonce`` cached that exception; and
the telemetry dependency tracker logged a chained traceback per imported
module per heartbeat per worker (gigabytes of stderr per CI job).
"""

from __future__ import annotations

import importlib.metadata._adapters as _meta_adapters
import logging
from pathlib import Path

import pytest


@pytest.fixture
def reset_packages_caches():
    """Drop ``@callonce`` results and the bad-dist warning dedup set so each
    test exercises the cache-miss path.
    """
    from ddtrace.internal import packages as _p

    for fn in (_p.get_distributions, _p._package_for_root_module_mapping):
        inner = getattr(fn, "__wrapped__", None) or (fn.__closure__[0].cell_contents if fn.__closure__ else None)
        if inner is not None and hasattr(inner, "__callonce_result__"):
            del inner.__callonce_result__
    _p._BAD_DISTS_WARNED.clear()
    yield
    _p._BAD_DISTS_WARNED.clear()


@pytest.fixture
def isolated_metadata_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Restrict ``importlib.metadata.distributions()`` to a single directory."""
    import importlib.metadata as importlib_metadata

    def _fixed_path(*args, **kwargs):
        kwargs.setdefault("path", [str(tmp_path)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed_path))
    return tmp_path


@pytest.fixture
def strict_metadata_getitem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``Message.__getitem__`` to raise ``KeyError`` on missing keys.

    Mirrors the behavior of the ``importlib_metadata`` backport (today) and
    the future-strict path CPython is migrating to (``"Implicit None on
    return values is deprecated and will raise KeyErrors."``). Without this
    fixture the test would depend on the running Python's deprecation
    policy.
    """
    real_get = _meta_adapters.email.message.Message.__getitem__

    def strict(self, item):
        res = real_get(self, item)
        if res is None:
            raise KeyError(item)
        return res

    monkeypatch.setattr(_meta_adapters.Message, "__getitem__", strict)


def _write_dist_info(root: Path, name: str, version: str, metadata_body: str | None = None) -> Path:
    di = root / f"{name.replace('-', '_')}-{version}.dist-info"
    di.mkdir(parents=True)
    if metadata_body is None:
        metadata_body = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    (di / "METADATA").write_text(metadata_body)
    (di / "RECORD").write_text("")
    return di


def test_get_distributions_skips_bad_dist_warns_once_returns_partial_map(
    isolated_metadata_path: Path,
    reset_packages_caches,
    strict_metadata_getitem,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Single load-bearing test. Asserts:

    1. The function does not raise on a malformed dist (so ``@callonce``
       caches a *result*, not an exception — the regression that produced
       the customer's per-module log spam).
    2. The good dist is still returned (partial-map behavior).
    3. Exactly one warning is emitted across multiple calls (the dedup,
       which is what bounds the operator-facing log volume on hosts where
       a malformed dist is permanently installed).
    """
    _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")
    _write_dist_info(
        isolated_metadata_path,
        "broken-pkg",
        "2.0",
        metadata_body="Metadata-Version: 2.1\nVersion: 2.0\n",  # no Name:
    )

    from ddtrace.internal.packages import get_distributions

    with caplog.at_level(logging.WARNING, logger="ddtrace.internal.packages"):
        a = get_distributions()
        b = get_distributions()
        c = get_distributions()

    assert a == b == c
    assert a["good-pkg"] == "1.0"
    assert "broken-pkg" not in a

    bad_dist_warnings = [r for r in caplog.records if "Skipping distribution" in r.getMessage()]
    assert len(bad_dist_warnings) == 1


def test_package_for_root_module_mapping_skips_bad_dist(
    isolated_metadata_path: Path,
    reset_packages_caches,
    strict_metadata_getitem,
) -> None:
    """The pre-fix top-level ``try/except`` collapsed the entire mapping to
    ``None`` on one bad dist, silently making ``filename_to_package`` /
    ``is_third_party`` fall back to "everything is user code" for the rest
    of the process. Per-dist tolerance keeps the mapping intact.
    """
    di_good = _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")
    (di_good / "RECORD").write_text("good_pkg/__init__.py,,\n")
    (isolated_metadata_path / "good_pkg").mkdir()
    (isolated_metadata_path / "good_pkg" / "__init__.py").write_text("")

    di_bad = _write_dist_info(
        isolated_metadata_path,
        "broken-pkg",
        "2.0",
        metadata_body="Metadata-Version: 2.1\nVersion: 2.0\n",
    )
    (di_bad / "RECORD").write_text("broken_pkg/__init__.py,,\n")
    (isolated_metadata_path / "broken_pkg").mkdir()
    (isolated_metadata_path / "broken_pkg" / "__init__.py").write_text("")

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping is not None
    assert "good_pkg" in mapping
    assert mapping["good_pkg"].name == "good-pkg"
