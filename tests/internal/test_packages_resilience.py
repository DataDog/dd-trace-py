"""Resilience tests for ``ddtrace.internal.packages``.

Real-world environments (Linux distro Python, ``pip install -e`` from old
pip versions, conda-pip mixes, CI base images) ship dist-info directories
with malformed or unreadable METADATA. Before the fix, a single bad dist
made ``get_distributions`` raise; ``@callonce`` cached that exception; and
the telemetry dependency tracker logged a chained traceback per imported
module per heartbeat per worker (gigabytes of stderr per CI job).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


# ``importlib.metadata._adapters`` is the private module that holds the
# ``Message`` shim whose ``__getitem__`` returns ``None`` on missing keys
# today and is on a path to raising ``KeyError`` (CPython issue 102117 +
# the ``importlib_metadata`` backport already raises). It was introduced
# in Python 3.10; on 3.9 the strict-future regression we exercise here
# cannot be reproduced via that hook, so the tests below are skipped.
try:
    import importlib.metadata._adapters as _meta_adapters  # type: ignore[import-not-found]
except ImportError:  # Python 3.9
    _meta_adapters = None  # type: ignore[assignment]


@pytest.fixture
def reset_packages_caches():
    """Drop ``@callonce`` results and the bad-dist dedup set on both setup
    and teardown — these tests populate the caches with fixture site-packages
    that must not bleed into adjacent tests in the same worker.
    """
    from ddtrace.internal import packages as _p

    def _clear() -> None:
        for fn in (_p.get_distributions, _p._package_for_root_module_mapping, _p._bazel_manifest_site_packages):
            inner = getattr(fn, "__wrapped__", None) or (fn.__closure__[0].cell_contents if fn.__closure__ else None)
            if inner is not None and hasattr(inner, "__callonce_result__"):
                del inner.__callonce_result__
        _p._BAD_DISTS_WARNED.clear()

    _clear()
    yield
    _clear()


@pytest.fixture
def isolated_metadata_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Restrict ``importlib.metadata.distributions()`` to a single directory.

    Also marks ``tmp_path`` as the Bazel runfiles root: the no-RECORD directory
    scan (Strategy 2) is intentionally gated to verified runfiles dirs, so these
    Bazel-mirroring fixtures must advertise themselves as runfiles.
    """
    import importlib.metadata as importlib_metadata

    def _fixed_path(*args, **kwargs):
        kwargs.setdefault("path", [str(tmp_path)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed_path))
    monkeypatch.setenv("RUNFILES_DIR", str(tmp_path))
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
    if _meta_adapters is None:
        pytest.skip("importlib.metadata._adapters is unavailable on this Python")
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


def _write_dist_info_no_record(root: Path, name: str, version: str, top_level: str | None = None) -> Path:
    """Write a dist-info *without* a RECORD file.

    Mirrors Bazel's ``rules_python``, which omits RECORD for hermeticity, so
    ``dist.files`` is falsy and the dist lands in the no-RECORD fallback path.
    """
    di = root / f"{name.replace('-', '_')}-{version}.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    if top_level is not None:
        (di / "top_level.txt").write_text(top_level + "\n")
    return di


def test_packages_distributions_failure_does_not_abort_fallback(
    isolated_metadata_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising ``packages_distributions()`` must not break the fallback.

    When one bad no-RECORD dist makes ``packages_distributions()`` raise,
    Strategy 1 should degrade to empty and Strategy 2 (directory scan) should
    still resolve the otherwise-good Bazel packages.
    """
    root = isolated_metadata_path
    _write_dist_info_no_record(root, "good-bazel-pkg", "1.0", top_level="good_bazel_pkg")
    (root / "good_bazel_pkg").mkdir()
    (root / "good_bazel_pkg" / "__init__.py").write_text("")

    from ddtrace.internal import packages as _p

    def _boom() -> dict:
        raise RuntimeError("packages_distributions blew up")

    monkeypatch.setattr(_p, "get_package_distributions", _boom)

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    assert mapping.get("good_bazel_pkg") is not None
    assert mapping["good_bazel_pkg"].name == "good-bazel-pkg"


def test_directory_scan_skipped_outside_bazel_runfiles(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-RECORD directory scan must not run outside Bazel runfiles.

    A no-RECORD dist's dir can hold its own ``.dist-info`` plus unrelated
    top-level code (minimal/layered site-packages). Outside a verified runfiles
    tree the single-``.dist-info`` guard is not a real isolation guarantee, so
    the scan is skipped and those unrelated siblings are not misattributed to
    the dist (which would mark app frames as third-party).
    """
    import importlib.metadata as importlib_metadata

    sp = tmp_path / "site-packages"
    sp.mkdir(parents=True)
    _write_dist_info_no_record(sp, "lonely-pkg", "1.0")
    (sp / "lonely_pkg").mkdir()
    (sp / "lonely_pkg" / "__init__.py").write_text("")
    # Unrelated top-level code that merely shares the directory.
    (sp / "unrelated_app").mkdir()
    (sp / "unrelated_app" / "__init__.py").write_text("")

    def _fixed(*args, **kwargs):
        kwargs.setdefault("path", [str(sp)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed))
    monkeypatch.delenv("RUNFILES_DIR", raising=False)
    monkeypatch.delenv("RUNFILES_MANIFEST_FILE", raising=False)

    from ddtrace.internal import packages as _p

    monkeypatch.setattr(_p, "get_package_distributions", lambda: {})

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    # Scan skipped entirely: neither the unrelated sibling nor the dist's own
    # package is mapped via the directory scan.
    assert "unrelated_app" not in mapping
    assert "lonely_pkg" not in mapping


def test_directory_scan_runs_in_bazel_runfiles_tree(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inside a ``*.runfiles`` tree the scan runs even without RUNFILES_DIR.

    Identifies the runfiles tree purely by the ``*.runfiles`` ancestor (only
    ``RUNFILES_MANIFEST_FILE`` is set), so the dist's package is mapped.
    """
    import importlib.metadata as importlib_metadata

    sp = tmp_path / "app.runfiles" / "lonely" / "site-packages"
    sp.mkdir(parents=True)
    _write_dist_info_no_record(sp, "lonely-pkg", "1.0")
    (sp / "lonely_pkg").mkdir()
    (sp / "lonely_pkg" / "__init__.py").write_text("")

    def _fixed(*args, **kwargs):
        kwargs.setdefault("path", [str(sp)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed))
    monkeypatch.delenv("RUNFILES_DIR", raising=False)
    monkeypatch.setenv("RUNFILES_MANIFEST_FILE", str(tmp_path / "app.runfiles_manifest"))

    from ddtrace.internal import packages as _p

    monkeypatch.setattr(_p, "get_package_distributions", lambda: {})

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    assert mapping["lonely_pkg"].name == "lonely-pkg"


def test_directory_scan_recognizes_manifest_mode_runfiles(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manifest-mode runfiles (``RUNFILES_MANIFEST_FILE`` only) are recognized.

    The dependency dir can sit outside any ``*.runfiles`` tree; its real path
    is listed in the MANIFEST. ``_bazel_manifest_site_packages`` extracts the
    site-packages root from the manifest so the scan still runs for it.
    """
    import importlib.metadata as importlib_metadata

    # Dependency dir deliberately NOT under a *.runfiles tree.
    sp = tmp_path / "execroot" / "dep" / "site-packages"
    sp.mkdir(parents=True)
    _write_dist_info_no_record(sp, "manifest-pkg", "1.0")
    (sp / "manifest_pkg").mkdir()
    (sp / "manifest_pkg" / "__init__.py").write_text("")

    manifest = tmp_path / "app.runfiles_manifest"
    manifest.write_text(f"app/manifest_pkg/__init__.py {sp / 'manifest_pkg' / '__init__.py'}\n")

    def _fixed(*args, **kwargs):
        kwargs.setdefault("path", [str(sp)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed))
    monkeypatch.delenv("RUNFILES_DIR", raising=False)
    monkeypatch.setenv("RUNFILES_MANIFEST_FILE", str(manifest))

    from ddtrace.internal import packages as _p

    monkeypatch.setattr(_p, "get_package_distributions", lambda: {})

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    assert mapping["manifest_pkg"].name == "manifest-pkg"


def test_bazel_manifest_parses_escaped_entries(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escaped manifest lines (paths with spaces/backslashes) must be parsed.

    Bazel writes a leading-space escaped form when a path contains a space,
    newline or backslash (common on Windows). Splitting naively on the first
    space drops the real path and the dependency dir is rejected. The escaped
    real path must be unescaped and its ``site-packages`` root recorded.
    """
    sp = tmp_path / "dir with space" / "site-packages"
    sp.mkdir(parents=True)
    target = sp / "pkg" / "__init__.py"

    # Bazel escaped form: " <escaped_link> <escaped_target>" with \b for
    # backslash and \s for space (backslash first so it is not double-escaped).
    def _escape(s: str) -> str:
        return s.replace("\\", r"\b").replace(" ", r"\s").replace("\n", r"\n")

    manifest = tmp_path / "app.runfiles_manifest"
    manifest.write_text(f" {_escape('app/pkg/__init__.py')} {_escape(str(target))}\n")

    monkeypatch.delenv("RUNFILES_DIR", raising=False)
    monkeypatch.setenv("RUNFILES_MANIFEST_FILE", str(manifest))

    from ddtrace.internal import packages as _p

    roots = _p._bazel_manifest_site_packages()

    assert str(sp.resolve()) in roots
    assert _p._is_bazel_runfiles_dir(sp)


def test_scan_skips_lone_unrelated_dist_info(
    isolated_metadata_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The directory scan must verify the lone .dist-info belongs to the dist.

    A no-RECORD ``.egg-info``/editable dist can live in a minimal shared
    site-packages whose only ``.dist-info`` belongs to an *unrelated* package.
    The single-.dist-info guard alone would pass and wrongly attribute every
    unmapped module in that dir to the egg dist.
    """
    root = isolated_metadata_path
    # An unrelated, regular dist provides the only .dist-info in the dir. It has
    # a real RECORD, so it resolves via the main loop, not the fallback.
    di_unrelated = _write_dist_info(root, "unrelated-pkg", "9.9")
    (di_unrelated / "RECORD").write_text("unrelated_pkg/__init__.py,,\n")
    (root / "unrelated_pkg").mkdir()
    (root / "unrelated_pkg" / "__init__.py").write_text("")
    # A no-RECORD egg-info dist sharing the same directory (the fallback path).
    egg = root / "editable_pkg.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: editable-pkg\nVersion: 0.1\n")
    # An unrelated top-level module that must NOT be attributed to editable-pkg.
    (root / "totally_unrelated").mkdir()
    (root / "totally_unrelated" / "__init__.py").write_text("")

    from ddtrace.internal import packages as _p

    monkeypatch.setattr(_p, "get_package_distributions", lambda: {})

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    # The egg's directory scan must be skipped: the lone .dist-info belongs to
    # unrelated-pkg, not editable-pkg.
    assert "totally_unrelated" not in mapping
    assert "unrelated_pkg" in mapping


def test_none_distribution_name_is_tolerated(
    isolated_metadata_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``None`` entry in packages_distributions values must not crash.

    Some environments yield ``None`` in the distribution-name list; calling
    ``.lower()`` on it used to raise outside the per-dist guard and collapse
    the whole mapping.
    """
    root = isolated_metadata_path
    _write_dist_info_no_record(root, "good-bazel-pkg", "1.0")
    (root / "good_bazel_pkg").mkdir()
    (root / "good_bazel_pkg" / "__init__.py").write_text("")

    from ddtrace.internal import packages as _p

    monkeypatch.setattr(
        _p,
        "get_package_distributions",
        lambda: {"somemod": [None], "good_bazel_pkg": ["good-bazel-pkg"]},
    )

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    assert mapping.get("good_bazel_pkg") is not None
    assert mapping["good_bazel_pkg"].name == "good-bazel-pkg"


def test_filename_to_package_resolves_shared_intermediate_namespace(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end lookup must attribute each shared-namespace file correctly.

    The regression that kept recurring: the directory scan stores deep keys
    (``google/cloud/storage`` / ``google/cloud/bigquery``) but ``_root_module``
    only yields the fixed 2-level key ``google/cloud``, so ``filename_to_package``
    resolved every ``google/cloud/...`` file to whichever dist was scanned
    first. With longest-prefix matching each file resolves to its own dist.
    """
    from ddtrace.internal import packages as _p

    # Lay both dists under a common ``site-packages`` parent so the Bazel
    # runfiles heuristic in _relative_to_known_root resolves the files.
    sp = tmp_path / "runfiles" / "site-packages"
    sp.mkdir(parents=True)
    (sp / "google" / "cloud" / "storage").mkdir(parents=True)
    (sp / "google" / "cloud" / "storage" / "__init__.py").write_text("")
    (sp / "google" / "cloud" / "storage" / "blob.py").write_text("")
    (sp / "google" / "cloud" / "bigquery").mkdir(parents=True)
    (sp / "google" / "cloud" / "bigquery" / "__init__.py").write_text("")
    (sp / "google" / "cloud" / "bigquery" / "client.py").write_text("")

    mapping = {
        "google/cloud/storage": _p.Distribution(name="google-cloud-storage", version="1.0"),
        "google/cloud/bigquery": _p.Distribution(name="google-cloud-bigquery", version="2.0"),
    }
    monkeypatch.setattr(_p, "_package_for_root_module_mapping", lambda: mapping)
    _p.filename_to_package.cache_clear()

    storage_pkg = _p.filename_to_package(sp / "google" / "cloud" / "storage" / "blob.py")
    bigquery_pkg = _p.filename_to_package(sp / "google" / "cloud" / "bigquery" / "client.py")

    assert storage_pkg is not None and storage_pkg.name == "google-cloud-storage"
    assert bigquery_pkg is not None and bigquery_pkg.name == "google-cloud-bigquery"

    _p.filename_to_package.cache_clear()


def test_filename_to_package_does_not_attribute_source_roots_to_dependency(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep prefix matching must not leak dependency namespaces onto user code.

    In Bazel a binary's own source/workspace roots are on ``sys.path`` too. A
    user file ``<workspace>/google/cloud/storage/app.py`` shares the namespace
    prefix of a ``google-cloud-storage`` dependency, but it is NOT under a
    ``site-packages`` root, so it must resolve to user code (None), not the
    dependency.
    """
    from ddtrace.internal import packages as _p

    workspace = tmp_path / "workspace"
    (workspace / "google" / "cloud" / "storage").mkdir(parents=True)
    (workspace / "google" / "cloud" / "storage" / "app.py").write_text("")

    mapping = {"google/cloud/storage": _p.Distribution(name="google-cloud-storage", version="1.0")}
    monkeypatch.setattr(_p, "_package_for_root_module_mapping", lambda: mapping)
    # The workspace root is on sys.path, mirroring a Bazel py_binary.
    monkeypatch.setattr(_p, "resolve_sys_path", lambda: [workspace])
    _p.filename_to_package.cache_clear()

    pkg = _p.filename_to_package(workspace / "google" / "cloud" / "storage" / "app.py")

    assert pkg is None

    _p.filename_to_package.cache_clear()


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

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.packages"):
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
