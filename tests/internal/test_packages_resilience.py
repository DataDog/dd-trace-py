"""Resilience tests for ``ddtrace.internal.packages``.

These cover the case where one or more installed distributions ship with
malformed or unreadable METADATA / PKG-INFO. Real-world examples include
Linux distro-managed Python packages (apt/RPM), editable installs from old
pip versions, and conda-pip mixes; they're particularly common on CI base
images that mount many packages from heterogeneous sources.

Before the fix, a single bad dist made ``get_distributions`` raise; the
``@callonce`` cache then permanently poisoned the function for the lifetime
of the process; and the telemetry dependency tracker logged a chained
``AttributeError``/``KeyError`` traceback per imported module per heartbeat
per worker (gigabytes of stderr per CI job).

The fix is per-dist defensive: skip malformed entries, return what could be
parsed, warn once per bad dist.
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
    """Restrict ``importlib.metadata.distributions()`` to a single directory.

    Patching the default-path resolution lets each test build a known-good
    fixture site-packages without depending on whatever happens to be
    installed in the test environment.
    """
    import importlib.metadata as importlib_metadata

    def _fixed_path(*args, **kwargs):
        kwargs.setdefault("path", [str(tmp_path)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed_path))
    return tmp_path


def _write_dist_info(root: Path, name: str, version: str, metadata_body: str | None = None) -> Path:
    di = root / f"{name.replace('-', '_')}-{version}.dist-info"
    di.mkdir(parents=True)
    if metadata_body is None:
        metadata_body = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    (di / "METADATA").write_text(metadata_body)
    (di / "RECORD").write_text("")
    return di


def test_get_distributions_skips_dist_missing_name_header(
    isolated_metadata_path: Path,
    reset_packages_caches,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One bad dist with no Name: header must not abort the whole call."""
    _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")
    _write_dist_info(
        isolated_metadata_path,
        "broken-pkg",
        "2.0",
        metadata_body="Metadata-Version: 2.1\nVersion: 2.0\n",  # no Name:
    )

    from ddtrace.internal.packages import get_distributions

    with caplog.at_level(logging.WARNING, logger="ddtrace.internal.packages"):
        result = get_distributions()

    assert "good-pkg" in result
    assert "broken-pkg" not in result
    assert result["good-pkg"] == "1.0"


def test_get_distributions_warns_once_per_bad_dist_across_calls(
    isolated_metadata_path: Path,
    reset_packages_caches,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if a future Python escalates the deprecation to KeyError (the
    ``importlib_metadata`` backport already does), we should warn once and
    keep going, not log per-call.
    """
    _write_dist_info(
        isolated_metadata_path,
        "broken-pkg",
        "2.0",
        metadata_body="Metadata-Version: 2.1\nVersion: 2.0\n",
    )

    # Force the future-strict path so the test doesn't depend on the running
    # Python's deprecation policy.
    real_get = _meta_adapters.email.message.Message.__getitem__

    def strict_get(self, item):
        res = real_get(self, item)
        if res is None:
            raise KeyError(item)
        return res

    monkeypatch.setattr(_meta_adapters.Message, "__getitem__", strict_get)

    from ddtrace.internal import packages as _p
    from ddtrace.internal.packages import get_distributions

    with caplog.at_level(logging.WARNING, logger="ddtrace.internal.packages"):
        get_distributions()
        get_distributions()
        get_distributions()

    bad_dist_warnings = [r for r in caplog.records if "Skipping distribution" in r.getMessage()]
    assert len(bad_dist_warnings) == 1, (
        f"expected exactly one bad-dist warning across 3 calls, got {len(bad_dist_warnings)}"
    )
    assert _p._BAD_DISTS_WARNED, "dedup set should record the bad dist"


def test_get_distributions_does_not_cache_failure_when_one_dist_is_bad(
    isolated_metadata_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-fix bug: ``@callonce`` caches the *exception* if the function
    raises. With per-dist tolerance the function returns a partial dict
    instead of raising, so subsequent calls return the same dict (cached
    *result*, not exception).
    """
    _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")
    _write_dist_info(
        isolated_metadata_path,
        "broken-pkg",
        "2.0",
        metadata_body="Metadata-Version: 2.1\nVersion: 2.0\n",
    )

    real_get = _meta_adapters.email.message.Message.__getitem__

    def strict_get(self, item):
        res = real_get(self, item)
        if res is None:
            raise KeyError(item)
        return res

    monkeypatch.setattr(_meta_adapters.Message, "__getitem__", strict_get)

    from ddtrace.internal.packages import get_distributions

    a = get_distributions()
    b = get_distributions()

    assert a == b
    assert a["good-pkg"] == "1.0"
    assert "broken-pkg" not in a


def test_get_distributions_handles_metadata_attribute_errors(
    isolated_metadata_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dist.metadata`` itself can raise (e.g. unreadable bytes, transient
    IOError during interpreter teardown). Skip and continue.
    """
    import importlib.metadata as importlib_metadata

    _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")

    original_metadata = importlib_metadata.Distribution.metadata.fget
    counter = {"called": 0}

    def flaky_metadata(self):
        counter["called"] += 1
        if counter["called"] == 1:
            raise OSError("simulated unreadable METADATA")
        return original_metadata(self)

    monkeypatch.setattr(
        importlib_metadata.Distribution,
        "metadata",
        property(flaky_metadata),
    )

    from ddtrace.internal.packages import get_distributions

    # First dist iteration raises OSError; second iteration returns valid metadata.
    # We can't assert on which dist is which deterministically, but the call
    # must not raise and must return a (possibly empty) mapping.
    result = get_distributions()
    assert isinstance(result, dict)


def test_package_for_root_module_mapping_skips_bad_dist(
    isolated_metadata_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-fix: one bad dist made the entire mapping return None, which
    silently broke ``filename_to_package`` / ``is_third_party`` for the rest
    of the process. Post-fix: the mapping is built from the remaining good
    dists.
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

    real_get = _meta_adapters.email.message.Message.__getitem__

    def strict_get(self, item):
        res = real_get(self, item)
        if res is None:
            raise KeyError(item)
        return res

    monkeypatch.setattr(_meta_adapters.Message, "__getitem__", strict_get)

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping is not None, "one bad dist should not collapse the whole mapping"
    assert "good_pkg" in mapping
    assert mapping["good_pkg"].name == "good-pkg"
