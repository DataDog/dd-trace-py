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
import os
from pathlib import Path
import sys

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
        _p._reset_installed_distributions()
        _p._BAD_DISTS_WARNED.clear()
        _p._MAPPING_FAILURE_LOGGED = False

    _clear()
    yield
    _clear()


@pytest.fixture
def isolated_metadata_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Restrict distribution discovery to a single directory.

    Both importlib.metadata.distributions() and the native sys.path scan behind
    _package_for_root_module_mapping are restricted.
    """
    import importlib.metadata as importlib_metadata

    def _fixed_path(*args, **kwargs):
        kwargs.setdefault("path", [str(tmp_path)])
        return importlib_metadata.MetadataPathFinder.find_distributions(
            importlib_metadata.DistributionFinder.Context(**kwargs)
        )

    monkeypatch.setattr(importlib_metadata.Distribution, "discover", staticmethod(_fixed_path))
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
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


def _prefetch_and_wait(packages) -> None:
    """Start the background prefetch and wait for it, for tests that inspect its effects."""
    packages.prefetch_distributions()
    if (thread := packages._PREFETCH_THREAD) is not None:
        thread.join()


def _write_dist_info(root: Path, name: str, version: str, metadata_body: str | None = None) -> Path:
    di = root / f"{name.replace('-', '_')}-{version}.dist-info"
    di.mkdir(parents=True)
    if metadata_body is None:
        metadata_body = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    (di / "METADATA").write_text(metadata_body)
    (di / "RECORD").write_text("")
    return di


def test_filename_to_package_resolves_shared_intermediate_namespace(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end lookup must attribute each shared-namespace file correctly.

    The regression that kept recurring: the directory scan stores deep keys
    (google/cloud/storage / google/cloud/bigquery) but _root_module
    only yields the fixed 2-level key google/cloud, so filename_to_package
    resolved every google/cloud/... file to whichever dist was scanned
    first. With longest-prefix matching, each file resolves to its own dist.
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

    In Bazel a binary's own source/workspace roots are on sys.path too. A
    user file <workspace>/google/cloud/storage/app.py shares the namespace
    prefix of a google-cloud-storage dependency, but it is not under a
    site-packages root, so it must resolve to user code.
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


def test_filename_to_package_resolves_namespace_on_non_site_packages_install_root(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendored namespace deps on a non-site-packages root must still resolve.

    A distribution installed with pip install --target=/app/vendor (or
    vendored onto PYTHONPATH) lives on a sys.path root that is not named
    site-packages. The mapping stores the deep key google/cloud/storage,
    so the anchored longest-prefix lookup must recognize the target dir as an
    install root -- it ships the *.dist-info -- and attribute the file to
    the dependency rather than falling through to user code.
    """
    from ddtrace.internal import packages as _p

    vendor = tmp_path / "vendor"
    vendor.mkdir()
    # The dist-info marks vendor as an install root (not a source root).
    _write_dist_info(vendor, "google-cloud-storage", "1.0")
    (vendor / "google" / "cloud" / "storage").mkdir(parents=True)
    (vendor / "google" / "cloud" / "storage" / "blob.py").write_text("")

    mapping = {"google/cloud/storage": _p.Distribution(name="google-cloud-storage", version="1.0")}
    monkeypatch.setattr(_p, "_package_for_root_module_mapping", lambda: mapping)
    monkeypatch.setattr(_p, "resolve_sys_path", lambda: [vendor])
    _p._is_install_root.cache_clear()
    _p.filename_to_package.cache_clear()

    pkg = _p.filename_to_package(vendor / "google" / "cloud" / "storage" / "blob.py")

    assert pkg is not None and pkg.name == "google-cloud-storage"

    _p._is_install_root.cache_clear()
    _p.filename_to_package.cache_clear()


def test_mapping_generates_deep_keys_for_shared_namespace_dists(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    """The generator must key shared-namespace dists on their deepest import
    root, not a fixed 2-level prefix.

    ``google-cloud-storage`` and ``google-cloud-bigquery`` both live under the
    ``google/cloud`` PEP 420 namespace. Keying on ``google/cloud`` collapses
    both onto whichever dist is scanned first, which is exactly what
    filename_to_package's longest-prefix lookup exists to avoid. The mapping
    must therefore contain ``google/cloud/storage`` and ``google/cloud/bigquery``
    and must not contain the ambiguous ``google/cloud`` key.
    """

    def _write_namespace_dist(name: str, version: str, leaf: str, module: str) -> None:
        di = _write_dist_info(isolated_metadata_path, name, version)
        pkg_dir = isolated_metadata_path / "google" / "cloud" / leaf
        pkg_dir.mkdir(parents=True, exist_ok=True)
        # Namespace levels (google, google/cloud) intentionally lack __init__.py.
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / module).write_text("")
        (di / "RECORD").write_text(f"google/cloud/{leaf}/__init__.py,,\ngoogle/cloud/{leaf}/{module},,\n")

    _write_namespace_dist("google-cloud-storage", "1.0", "storage", "blob.py")
    _write_namespace_dist("google-cloud-bigquery", "2.0", "bigquery", "client.py")

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping is not None
    assert "google/cloud" not in mapping
    assert mapping["google/cloud/storage"].name == "google-cloud-storage"
    assert mapping["google/cloud/bigquery"].name == "google-cloud-bigquery"


def test_mapping_keeps_module_filename_for_flat_namespace_dists(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    """Module files shipped directly under a shared PEP 420 namespace must keep
    distinct keys.

    Two dists can drop plain modules into the same namespace with no
    __init__.py at any level (dist A ships acme/foo.py, dist B ships
    acme/bar.py). Keying both on the bare ``acme`` prefix collapses them
    onto whichever dist is scanned first; the key must therefore retain the
    module file name so each module resolves to its own distribution.
    """

    def _write_flat_namespace_dist(name: str, version: str, module: str) -> None:
        di = _write_dist_info(isolated_metadata_path, name, version)
        acme = isolated_metadata_path / "acme"
        acme.mkdir(exist_ok=True)
        # acme is a namespace: no __init__.py, only sibling module files.
        (acme / module).write_text("")
        (di / "RECORD").write_text(f"acme/{module},,\n")

    _write_flat_namespace_dist("acme-foo", "1.0", "foo.py")
    _write_flat_namespace_dist("acme-bar", "2.0", "bar.py")

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping is not None
    assert "acme" not in mapping
    assert mapping["acme/foo.py"].name == "acme-foo"
    assert mapping["acme/bar.py"].name == "acme-bar"


def test_get_distributions_skips_bad_dist_warns_once_returns_partial_map(
    isolated_metadata_path: Path,
    reset_packages_caches,
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
    broken = _write_dist_info(isolated_metadata_path, "broken-pkg", "2.0")
    # Not valid UTF-8, so the metadata cannot be read at all.
    (broken / "METADATA").write_bytes(b"Metadata-Version: 2.1\nName: broken\xff\nVersion: 2.0\n")

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


def test_filename_to_package_does_not_attribute_editable_source_root_to_dependency(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editable checkout's own .egg-info must not license unrelated matches.

    A legacy editable install (``pip install -e .`` / ``setup.py develop``)
    drops the project's own ``<name>.egg-info`` in the source root, which is on
    sys.path. That metadata belongs to the project, not to a third-party
    namespace dependency, so a user file such as
    ``<repo>/google/cloud/storage/app.py`` must still resolve to user code
    (None) -- the install-root anchor only counts when the root ships the
    matched distribution's own metadata.
    """
    from ddtrace.internal import packages as _p

    repo = tmp_path / "repo"
    repo.mkdir()
    # The repo carries only its own project metadata, not google-cloud-storage.
    (repo / "myproject.egg-info").mkdir()
    (repo / "myproject.egg-info" / "PKG-INFO").write_text("Name: myproject\nVersion: 1.0\n")
    (repo / "google" / "cloud" / "storage").mkdir(parents=True)
    (repo / "google" / "cloud" / "storage" / "app.py").write_text("")

    mapping = {"google/cloud/storage": _p.Distribution(name="google-cloud-storage", version="1.0")}
    monkeypatch.setattr(_p, "_package_for_root_module_mapping", lambda: mapping)
    monkeypatch.setattr(_p, "resolve_sys_path", lambda: [repo])
    _p._is_install_root.cache_clear()
    _p.filename_to_package.cache_clear()

    pkg = _p.filename_to_package(repo / "google" / "cloud" / "storage" / "app.py")

    assert pkg is None

    _p._is_install_root.cache_clear()
    _p.filename_to_package.cache_clear()


def test_mapping_ignores_listed_files_missing_on_disk(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    """A RECORD entry whose file is gone must not claim its key.

    importlib.metadata drops missing files from Distribution.files; the native
    scan only stats one file per key, so a stale entry must not slip through.
    """
    di = _write_dist_info(isolated_metadata_path, "stale-pkg", "1.0")
    (isolated_metadata_path / "present.py").write_text("")
    (di / "RECORD").write_text("present.py,,\ngone.py,,\n")

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping is not None
    assert mapping["present.py"].name == "stale-pkg"
    assert "gone.py" not in mapping


def test_mapping_reads_egg_info_installed_files(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    """installed-files.txt entries are relative to the .egg-info directory."""
    ei = isolated_metadata_path / "legacy_pkg-1.0.egg-info"
    ei.mkdir()
    (ei / "PKG-INFO").write_text("Metadata-Version: 1.1\nName: legacy-pkg\nVersion: 1.0\n")
    (ei / "installed-files.txt").write_text("../legacy_pkg/__init__.py\n../legacy_pkg/core.py\nPKG-INFO\n")
    (isolated_metadata_path / "legacy_pkg").mkdir()
    (isolated_metadata_path / "legacy_pkg" / "__init__.py").write_text("")
    (isolated_metadata_path / "legacy_pkg" / "core.py").write_text("")

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping == {"legacy_pkg": ("legacy-pkg", "1.0")}


def test_mapping_reads_zip_entries(
    tmp_path: Path,
    reset_packages_caches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zip archives on sys.path (zipapps, PEX, vendored deps) count like directories."""
    import zipfile

    archive = tmp_path / "deps.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("zipped_pkg/__init__.py", "")
        zf.writestr("zipped_pkg-1.0.dist-info/METADATA", "Name: zipped-pkg\nVersion: 1.0\n")
        zf.writestr("zipped_pkg-1.0.dist-info/RECORD", "zipped_pkg/__init__.py,,\n")
    monkeypatch.setattr(sys, "path", [str(archive)])

    from ddtrace.internal.packages import _package_for_root_module_mapping

    mapping = _package_for_root_module_mapping()

    assert mapping == {"zipped_pkg": ("zipped-pkg", "1.0")}


def test_native_bad_metadata_warns_once(
    isolated_metadata_path: Path,
    reset_packages_caches,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Undecodable METADATA skips only that distribution, with one debug log."""
    good = _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")
    (good / "RECORD").write_text("good.py,,\n")
    (isolated_metadata_path / "good.py").write_text("")
    bad = _write_dist_info(isolated_metadata_path, "bad-pkg", "1.0")
    (bad / "METADATA").write_bytes(b"Name: bad\xff\nVersion: 1.0\n")
    (bad / "RECORD").write_text("bad.py,,\n")
    (isolated_metadata_path / "bad.py").write_text("")

    from ddtrace.internal.packages import _package_for_root_module_mapping

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.packages"):
        mapping = _package_for_root_module_mapping()

    assert mapping == {"good.py": ("good-pkg", "1.0")}
    assert len([r for r in caplog.records if "Skipping distribution" in r.getMessage()]) == 1


def test_package_distributions_infers_top_level_names(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    """Without top_level.txt, importable names are inferred from existing files,
    like importlib.metadata.packages_distributions does.
    """
    from importlib.machinery import EXTENSION_SUFFIXES

    di = _write_dist_info(isolated_metadata_path, "inferred-pkg", "1.0")
    ext = f"_speedups{EXTENSION_SUFFIXES[0]}"
    for name in ("inferred/__init__.py", "solo.py", ext, "inferred.pth"):
        target = isolated_metadata_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    (di / "RECORD").write_text(
        f"inferred/__init__.py,,\nsolo.py,,\n{ext},,\ninferred.pth,,\nmissing.py,,\n{di.name}/METADATA,,\n"
    )

    from ddtrace.internal.packages import get_package_distributions

    pkgs = get_package_distributions()

    assert pkgs == {"inferred": ["inferred-pkg"], "solo": ["inferred-pkg"], "_speedups": ["inferred-pkg"]}


def test_package_distributions_prefers_declared_top_level(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    di = _write_dist_info(isolated_metadata_path, "declared-pkg", "1.0")
    (isolated_metadata_path / "actual.py").write_text("")
    (di / "RECORD").write_text("actual.py,,\n")
    (di / "top_level.txt").write_text("declared\nother\n")

    from ddtrace.internal.packages import get_package_distributions

    assert get_package_distributions() == {"declared": ["declared-pkg"], "other": ["declared-pkg"]}


def test_maps_handle_partial_metadata(
    isolated_metadata_path: Path,
    reset_packages_caches,
) -> None:
    """A dist without a version still maps its packages but has no version to
    report, and a dist without a file list still reports its version.
    """
    unversioned = _write_dist_info(isolated_metadata_path, "unversioned", "0", metadata_body="Name: unversioned\n")
    (isolated_metadata_path / "unversioned.py").write_text("")
    (unversioned / "RECORD").write_text("unversioned.py,,\n")
    fileless_info = _write_dist_info(isolated_metadata_path, "fileless", "3.0")
    (fileless_info / "RECORD").unlink()

    from ddtrace.internal.packages import _package_for_root_module_mapping
    from ddtrace.internal.packages import get_distributions
    from ddtrace.internal.packages import get_package_distributions

    assert get_distributions() == {"fileless": "3.0"}
    assert get_package_distributions() == {"unversioned": ["unversioned"]}
    assert _package_for_root_module_mapping() == {}


@pytest.mark.skipif(sys.version_info < (3, 12), reason="importlib keeps missing files before Python 3.12")
def test_native_scan_matches_importlib(reset_packages_caches) -> None:
    """The native scan must derive the same maps as importlib on a real environment."""
    import importlib.metadata as importlib_metadata

    from ddtrace.internal import packages as _p

    expected_versions: dict = {}
    expected_pkgs: dict = {}
    expected_mapping: dict = {}
    for entry in dict.fromkeys(e for e in sys.path if isinstance(e, str)):
        for name, version, keys, top_level in _p._python_dist_records(importlib_metadata.distributions(path=[entry])):
            for pkg in top_level:
                expected_pkgs.setdefault(pkg, []).append(name)
            if version is None:
                continue
            expected_versions[name.lower()] = version
            for key in keys:
                expected_mapping.setdefault(key, (name, version))

    mapping = _p._package_for_root_module_mapping()

    assert mapping is not None
    assert {k: tuple(v) for k, v in mapping.items()} == expected_mapping
    assert dict(_p.get_distributions()) == expected_versions
    assert {k: sorted(v) for k, v in _p.get_package_distributions().items()} == {
        k: sorted(v) for k, v in expected_pkgs.items()
    }
    # And the importable names agree with the stdlib implementation itself.
    assert set(_p.get_package_distributions()) == set(importlib_metadata.packages_distributions())


def test_prefetch_matches_lazy_scan(reset_packages_caches) -> None:
    """The background boot-time scan must yield exactly what a lazy scan does, in
    the same order: the first distribution to claim a key wins.
    """
    from ddtrace.internal import packages as _p

    lazy = _p._installed_distributions()
    _p._reset_installed_distributions()

    _prefetch_and_wait(_p)

    assert _p._installed_distributions() == lazy


def test_prefetch_never_raises(reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing scan at boot must not break the post-preload sequence."""
    from ddtrace.internal import packages as _p

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_p, "_distribution_records", boom)

    _prefetch_and_wait(_p)

    assert _p._INSTALLED is None


def test_concurrent_callers_scan_once(reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """The boot-time prefetch and a lazy caller must not both run the scan."""
    import threading
    import time

    from ddtrace.internal import packages as _p

    scans = []

    def slow_records(entries, segments, warn=None):
        scans.append(entries)
        time.sleep(0.2)
        return iter([("pkg", "1.0", ["pkg"], ["pkg"])])

    monkeypatch.setattr(_p, "_distribution_records", slow_records)

    results = []
    callers = [threading.Thread(target=lambda: results.append(_p._installed_distributions())) for _ in range(4)]
    for t in callers:
        t.start()
    for t in callers:
        t.join()

    assert len(scans) == 1
    assert results == [[("pkg", "1.0", ["pkg"], ["pkg"])]] * 4


def _lock_is_free(lock) -> bool:
    """Whether another thread could take lock right now (RLock has no locked())."""
    import threading

    free = []

    def probe():
        if lock.acquire(blocking=False):
            lock.release()
            free.append(True)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return bool(free)


def test_scan_warnings_are_logged_outside_the_lock(reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """Log handlers must not run under the scan lock, which is not gevent-aware."""
    from ddtrace.internal import packages as _p

    def records_with_problem(entries, segments, warn=None):
        warn("/site/bad.dist-info", "not utf-8")
        return iter([])

    free = []
    monkeypatch.setattr(_p, "_distribution_records", records_with_problem)
    monkeypatch.setattr(
        _p, "_warn_bad_dist", lambda dist, exc: free.append(_lock_is_free(_p._INSTALLED_DISTRIBUTIONS_LOCK))
    )

    _p._installed_distributions()

    assert free == [True]


def test_records_follow_sys_path_changes(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entries added to sys.path after the boot-time prefetch must still be seen
    when the maps are first built, without rescanning the entries already known.
    """
    from ddtrace.internal import packages as _p

    def site(name: str, dist: str, module: str) -> Path:
        root = tmp_path / name
        di = _write_dist_info(root, dist, "1.0")
        (root / f"{module}.py").write_text("")
        (di / "RECORD").write_text(f"{module}.py,,\n")
        return root

    boot = site("boot", "boot-dist", "shared")
    vendor = site("vendor", "vendor-dist", "shared")
    extra = site("extra", "extra-dist", "extra")
    monkeypatch.setattr(sys, "path", [str(boot)])

    scanned = []
    real_scan = _p.scan_distributions

    def counting_scan(entry, *args):
        scanned.append(entry)
        return real_scan(entry, *args)

    monkeypatch.setattr(_p, "scan_distributions", counting_scan)

    _prefetch_and_wait(_p)
    assert scanned == [str(boot)]

    # The application vendors its own copy ahead of site-packages, and adds a
    # plugin directory at the end.
    sys.path.insert(0, str(vendor))
    sys.path.append(str(extra))

    mapping = _p._package_for_root_module_mapping()

    assert scanned == [str(boot), str(vendor), str(extra)]
    assert mapping == {"shared.py": ("vendor-dist", "1.0"), "extra.py": ("extra-dist", "1.0")}

    # Unchanged sys.path: nothing is rescanned.
    _p._installed_distributions()
    assert len(scanned) == 3

    # A removed entry no longer contributes.
    sys.path.remove(str(vendor))
    assert [r[0] for r in _p._installed_distributions()] == ["boot-dist", "extra-dist"]
    assert len(scanned) == 3


def test_reentrant_read_during_scan_does_not_deadlock(reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """An import or exception hook that reads the package maps while the scan
    holds the lock, on the same thread, must not deadlock.
    """
    import threading

    from ddtrace.internal import packages as _p

    expected = _p._installed_distributions()
    _p._reset_installed_distributions()

    real_entry_records = _p._entry_records
    hooked = []
    nested = []

    def entry_records_with_hook(entry, *args):
        if not hooked:
            hooked.append(entry)
            # What ddtrace's error tracking hooks can do from within the
            # importlib fallback: is_third_party -> ... -> _installed_distributions.
            nested.append(_p._installed_distributions())
        return real_entry_records(entry, *args)

    monkeypatch.setattr(_p, "_entry_records", entry_records_with_hook)

    result = []
    t = threading.Thread(target=lambda: result.append(_p._installed_distributions()), daemon=True)
    t.start()
    t.join(30)

    assert not t.is_alive(), "deadlocked on the scan lock"
    assert result == [expected]
    assert nested == [expected]


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="needs fork")
def test_fork_while_scan_lock_held_does_not_deadlock_child(reset_packages_caches) -> None:
    """Lazy scans run on application threads; if another thread forks mid-scan,
    the child must not inherit a held lock.
    """
    import os
    import threading
    import time
    import warnings

    from ddtrace.internal import packages as _p

    held = threading.Event()
    release = threading.Event()

    def holder():
        with _p._INSTALLED_DISTRIBUTIONS_LOCK:
            held.set()
            release.wait(30)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(30)
    try:
        with warnings.catch_warnings():
            # Python warns about forking a multi-threaded process; that is the point.
            warnings.simplefilter("ignore", DeprecationWarning)
            pid = os.fork()
        if pid == 0:
            ok = _p._INSTALLED_DISTRIBUTIONS_LOCK.acquire(timeout=5)
            os._exit(0 if ok else 1)
        deadline = time.monotonic() + 30
        while (status := os.waitpid(pid, os.WNOHANG))[0] == 0:
            if time.monotonic() > deadline:
                os.kill(pid, 9)
                pytest.fail("fork child hung on the scan lock")
            time.sleep(0.05)
    finally:
        release.set()
        t.join()

    assert os.waitstatus_to_exitcode(status[1]) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="surrogate-escaped paths are a POSIX thing")
def test_undecodable_sys_path_entry_does_not_break_maps(isolated_metadata_path: Path, reset_packages_caches) -> None:
    """A sys.path entry that is not valid UTF-8 (a non-UTF-8 directory name) must
    not cost the records of every other entry.
    """
    good = _write_dist_info(isolated_metadata_path, "good-pkg", "1.0")
    (isolated_metadata_path / "good.py").write_text("")
    (good / "RECORD").write_text("good.py,,\n")
    sys.path.append(str(isolated_metadata_path / "pkg\udce9"))

    from ddtrace.internal.packages import _package_for_root_module_mapping
    from ddtrace.internal.packages import get_distributions

    assert _package_for_root_module_mapping() == {"good.py": ("good-pkg", "1.0")}
    assert get_distributions() == {"good-pkg": "1.0"}


def test_scan_imports_nothing(tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """No module may be imported during the scan: an import hook that reads the
    package maps mid-scan would cache maps built from incomplete records.
    """
    import zipfile

    archive = tmp_path / "deps.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zp/__init__.py", "")
        zf.writestr("zp-1.0.dist-info/METADATA", "Name: zp\nVersion: 1.0\n")
        zf.writestr("zp-1.0.dist-info/RECORD", "zp/__init__.py,,\n")
    egg = tmp_path / "legacy-1.0.egg"
    (egg / "EGG-INFO").mkdir(parents=True)
    (egg / "legacy").mkdir()
    (egg / "legacy" / "__init__.py").write_text("")
    (egg / "EGG-INFO" / "PKG-INFO").write_text("Name: legacy\nVersion: 1.0\n")
    (egg / "EGG-INFO" / "SOURCES.txt").write_text("legacy/__init__.py\n")
    monkeypatch.setattr(sys, "path", [str(archive), str(egg)])

    from ddtrace.internal import packages as _p

    before = set(sys.modules)
    records = _p._installed_distributions()

    assert set(sys.modules) == before
    assert [(r[0], r[2]) for r in records] == [("zp", ["zp"]), ("legacy", ["legacy"])]


def test_custom_finders_run_outside_the_lock(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distributions from custom meta path finders count, in meta path order, and
    are asked for without the scan lock held, since they run arbitrary Python.
    """
    import importlib.metadata as importlib_metadata

    from ddtrace.internal import packages as _p

    site = tmp_path / "site"
    di = _write_dist_info(site, "on-path", "1.0")
    (site / "on_path.py").write_text("")
    (di / "RECORD").write_text("on_path.py,,\n")
    custom = tmp_path / "custom"
    cdi = _write_dist_info(custom, "from-finder", "2.0")
    (custom / "from_finder.py").write_text("")
    (cdi / "RECORD").write_text("from_finder.py,,\n")

    lock_free = []

    class Finder:
        def find_spec(self, *args, **kwargs):
            return None

        def find_distributions(self, context):
            lock_free.append(_lock_is_free(_p._INSTALLED_DISTRIBUTIONS_LOCK))
            return [importlib_metadata.PathDistribution(cdi)]

    monkeypatch.setattr(sys, "path", [str(site)])
    monkeypatch.setattr(sys, "meta_path", [Finder()] + [f for f in sys.meta_path if f is _p.PathFinder])

    mapping = _p._package_for_root_module_mapping()

    assert lock_free == [True]
    assert list(mapping.items()) == [("from_finder.py", ("from-finder", "2.0")), ("on_path.py", ("on-path", "1.0"))]


def test_unreadable_zip_member_is_reported(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A zip member that cannot be read (here bzip2, which the native reader does
    not support) is reported like other unreadable metadata, not skipped quietly.
    """
    import zipfile

    archive = tmp_path / "deps.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ok/__init__.py", "")
        zf.writestr("ok-1.0.dist-info/METADATA", "Name: ok\nVersion: 1.0\n")
        zf.writestr("ok-1.0.dist-info/RECORD", "ok/__init__.py,,\n")
        zf.writestr("bz-1.0.dist-info/METADATA", "Name: bz\nVersion: 1.0\n", compress_type=zipfile.ZIP_BZIP2)
    monkeypatch.setattr(sys, "path", [str(archive)])

    from ddtrace.internal.packages import _package_for_root_module_mapping

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.packages"):
        mapping = _package_for_root_module_mapping()

    assert mapping == {"ok": ("ok", "1.0")}
    warnings = [r.getMessage() for r in caplog.records if "Skipping distribution" in r.getMessage()]
    assert len(warnings) == 1 and "bz-1.0.dist-info" in warnings[0]


def test_relative_entries_follow_the_working_directory(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative sys.path entry scanned at boot must not keep pointing at the old
    working directory if the application changes it before the maps are used.
    """
    from ddtrace.internal import packages as _p

    for name in ("before", "after"):
        _site_with_dist(tmp_path / name, f"{name}-dist", name)
    monkeypatch.setattr(sys, "path", [""])

    monkeypatch.chdir(tmp_path / "before")
    _prefetch_and_wait(_p)

    monkeypatch.chdir(tmp_path / "after")
    assert [r[0] for r in _p._installed_distributions()] == ["after-dist"]


def test_scan_in_daemon_thread_at_interpreter_exit() -> None:
    """A daemon thread can be mid-scan, with the GIL released, when the
    interpreter finalizes; it must not take the process down with it.
    """
    import subprocess
    import sysconfig

    code = """
import sys, threading, time
from importlib.machinery import all_suffixes
from ddtrace.internal.native import scan_distributions

suffixes = sorted(all_suffixes(), key=len, reverse=True)
scanning = threading.Event()

def scan_forever():
    while True:
        scanning.set()
        scan_distributions(sys.argv[1], suffixes)

threading.Thread(target=scan_forever, daemon=True).start()
scanning.wait()
time.sleep(0.05)
"""
    for _ in range(5):
        result = subprocess.run(
            [sys.executable, "-c", code, sysconfig.get_path("purelib")], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        assert "fatal" not in result.stderr.lower() and "panic" not in result.stderr.lower(), result.stderr


def _site_with_dist(root: Path, name: str, module: str) -> Path:
    di = _write_dist_info(root, name, "1.0")
    (root / f"{module}.py").write_text("")
    (di / "RECORD").write_text(f"{module}.py,,\n")
    return root


@pytest.mark.parametrize(
    "name,query", [("PyYAML", "pyyaml"), ("My_.Package", "MY-package"), ("My-Package", "my.package")]
)
@pytest.mark.parametrize("version", ["1.0", ""])
def test_distribution_version_from_snapshot(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch, name: str, query: str, version: str
) -> None:
    import builtins

    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path, name, "example_module")
    (site / f"{name.replace('-', '_')}-1.0.dist-info" / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )
    monkeypatch.setattr(sys, "path", [str(site)])
    real_import = builtins.__import__

    def no_metadata_import(name, *args, **kwargs):
        if name == "importlib.metadata":
            raise AssertionError("Distribution version lookup must not import metadata")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_metadata_import)
    assert _p.get_distribution_version(query) == version
    assert _p.get_distribution_version("missing-package") == ""


def test_distribution_version_uses_first_installation(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddtrace.internal import packages as _p

    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_dist_info(first, "My-Package", "1.0")
    dist = _write_dist_info(second, "my_package", "2.0")
    (second / "alias_module.py").write_text("")
    (dist / "RECORD").write_text("alias_module.py,,\n")
    monkeypatch.setattr(sys, "path", [str(first), str(second)])
    assert _p.get_distribution_version("my.package") == "1.0"
    assert _p.get_module_distribution_versions("alias_module.child") == ("my_package", "1.0")

    # Replacing sys.path must also refresh the normalized version index.
    monkeypatch.setattr(sys, "path", [str(second), str(first)])
    assert _p.get_distribution_version("MY-PACKAGE") == "2.0"
    assert _p.get_module_distribution_versions("alias_module.child") == ("my_package", "2.0")


@pytest.mark.parametrize("version", ["1.0", ""])
@pytest.mark.parametrize("dist_name", ["example-dist", "Example-Dist", "Flask", "PyYAML"])
def test_module_versions_without_importing_metadata(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch, version: str, dist_name: str
) -> None:
    """Telemetry can resolve a module whose distribution has a different name
    without importing metadata on its background thread.
    """
    import builtins

    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path, dist_name, "example_module")
    (site / f"{dist_name.replace('-', '_')}-1.0.dist-info" / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: {version}\n"
    )
    monkeypatch.setattr(sys, "path", [str(site)])
    _p.get_module_distribution_versions.cache_clear()
    real_import = builtins.__import__

    def no_metadata_import(name, *args, **kwargs):
        if name == "importlib.metadata":
            raise AssertionError("Module version lookup must not import metadata")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", no_metadata_import)
        assert _p.get_module_distribution_versions("example_module.child") == (dist_name, version)
    _p.get_module_distribution_versions.cache_clear()


def test_scan_does_not_reimport_importlib_metadata(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IAST drops importlib.metadata after boot for gevent; the prefetch, which runs
    later, must not import it back when there are no custom finders.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "plain", "plain")
    monkeypatch.setattr(sys, "path", [str(site)])
    others = [f for f in sys.meta_path if f is not _p.PathFinder and not hasattr(f, "find_distributions")]
    monkeypatch.setattr(sys, "meta_path", others + [_p.PathFinder])
    monkeypatch.delitem(sys.modules, "importlib.metadata")

    _prefetch_and_wait(_p)

    assert "importlib.metadata" not in sys.modules
    assert [r[0] for r in _p._installed_distributions()] == ["plain"]


def test_failed_entry_scan_is_retried(tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan that raised must not be cached as an empty entry."""
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "flaky", "flaky")
    other = _site_with_dist(tmp_path / "other", "other", "other")
    monkeypatch.setattr(sys, "path", [str(site)])

    real_scan = _p.scan_distributions
    failures = [RuntimeError("transient")]

    def flaky_scan(entry, *args):
        if failures:
            raise failures.pop()
        return real_scan(entry, *args)

    monkeypatch.setattr(_p, "scan_distributions", flaky_scan)

    assert _p._installed_distributions() == []
    sys.path.append(str(other))
    assert [r[0] for r in _p._installed_distributions()] == ["flaky", "other"]


def test_install_into_existing_entry_is_seen(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like importlib, notice packages installed into an entry scanned at boot,
    before the maps are first used.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "early", "early")
    monkeypatch.setattr(sys, "path", [str(site)])
    _prefetch_and_wait(_p)

    before = os.stat(site).st_mtime_ns
    _site_with_dist(site, "late", "late")
    if os.stat(site).st_mtime_ns == before:  # coarse file system timestamps
        os.utime(site, ns=(before + 1_000_000_000, before + 1_000_000_000))

    assert sorted(r[0] for r in _p._installed_distributions()) == ["early", "late"]


class _DistFinder:
    """A custom meta path finder that provides one distribution."""

    def __init__(self, path: Path, on_find=None) -> None:
        self.path = path
        self.on_find = on_find

    def find_spec(self, *args, **kwargs):
        return None

    def find_distributions(self, context):
        import importlib.metadata as importlib_metadata

        if self.on_find is not None:
            self.on_find()
        return [importlib_metadata.PathDistribution(self.path)]


def test_custom_finder_added_after_prefetch_is_seen(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "on-path", "on_path")
    custom = _site_with_dist(tmp_path / "custom", "from-finder", "from_finder")
    monkeypatch.setattr(sys, "path", [str(site)])
    monkeypatch.setattr(sys, "meta_path", [_p.PathFinder])
    _prefetch_and_wait(_p)

    sys.meta_path.insert(0, _DistFinder(custom / "from_finder-1.0.dist-info"))

    assert [r[0] for r in _p._installed_distributions()] == ["from-finder", "on-path"]


def test_custom_finder_reading_the_maps_does_not_recurse(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A custom finder whose code reads the package maps must not query itself
    again, and its distributions must still make it into the published records.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "on-path", "on_path")
    custom = _site_with_dist(tmp_path / "custom", "from-finder", "from_finder")
    nested = []
    finder = _DistFinder(
        custom / "from_finder-1.0.dist-info", on_find=lambda: nested.append(_p._installed_distributions())
    )
    monkeypatch.setattr(sys, "path", [str(site)])
    monkeypatch.setattr(sys, "meta_path", [finder, _p.PathFinder])

    records = _p._installed_distributions()

    assert [r[0] for r in records] == ["from-finder", "on-path"]
    # The nested read saw the native records only, and did not publish them.
    assert [[r[0] for r in n] for n in nested] == [["on-path"]]
    assert _p._installed_distributions() == records


class MetadataPathFinder:
    """Stands in for the importlib_metadata backport's sys.path distribution finder."""

    def find_spec(self, *args, **kwargs):
        return None

    def find_distributions(self, context):
        raise AssertionError("the native scan stands for this finder")


@pytest.mark.parametrize("with_path_finder", [False, True])
def test_metadata_path_finder_means_the_native_scan(
    with_path_finder: bool, tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A MetadataPathFinder discovers the sys.path distributions: on its own it
    must not hide them, and next to PathFinder it must not list them twice.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "on-path", "on_path")
    monkeypatch.setattr(sys, "path", [str(site)])
    meta_path = [_p.PathFinder, MetadataPathFinder()] if with_path_finder else [MetadataPathFinder()]
    monkeypatch.setattr(sys, "meta_path", meta_path)

    assert [r[0] for r in _p._installed_distributions()] == ["on-path"]


def test_custom_finder_reordered_after_prefetch_is_seen(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving a custom finder across PathFinder changes which distribution wins a
    shared import root, so the records must follow the new order.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "on-path", "shared")
    custom = _site_with_dist(tmp_path / "custom", "from-finder", "shared")
    finder = _DistFinder(custom / "from_finder-1.0.dist-info")
    monkeypatch.setattr(sys, "path", [str(site)])
    monkeypatch.setattr(sys, "meta_path", [_p.PathFinder, finder])
    _prefetch_and_wait(_p)
    assert [r[0] for r in _p._installed_distributions()] == ["on-path", "from-finder"]

    sys.meta_path[:] = [finder, _p.PathFinder]

    assert _p._package_for_root_module_mapping() == {"shared.py": ("from-finder", "1.0")}


def test_custom_finder_results_are_refreshed(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finder installed at boot can expose more distributions before the maps
    are first used; nothing signals that, so the first read asks it again.
    """
    import importlib.metadata as importlib_metadata

    from ddtrace.internal import packages as _p

    custom = _site_with_dist(tmp_path / "custom", "late-dist", "late")
    registry: list = []

    class RegistryFinder:
        def find_spec(self, *args, **kwargs):
            return None

        def find_distributions(self, context):
            return [importlib_metadata.PathDistribution(p) for p in registry]

    monkeypatch.setattr(sys, "path", [])
    monkeypatch.setattr(sys, "meta_path", [RegistryFinder(), _p.PathFinder])
    _prefetch_and_wait(_p)

    registry.append(custom / "late_dist-1.0.dist-info")

    assert [r[0] for r in _p._installed_distributions()] == ["late-dist"]


def test_maps_built_inside_a_finder_query_are_not_kept(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finder that reads the package maps while being queried sees records
    without its own distributions; those maps must not be cached.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "on-path", "on_path")
    custom = _site_with_dist(tmp_path / "custom", "from-finder", "from_finder")
    seen = []

    def read_maps():
        if not seen:
            seen.append((_p.get_distributions(), _p.get_package_distributions(), _p._package_for_root_module_mapping()))

    finder = _DistFinder(custom / "from_finder-1.0.dist-info", on_find=read_maps)
    monkeypatch.setattr(sys, "path", [str(site)])
    monkeypatch.setattr(sys, "meta_path", [finder, _p.PathFinder])

    mapping = _p._package_for_root_module_mapping()

    # The nested reads saw the native records only ...
    versions, pkgs, nested_mapping = seen[0]
    assert dict(versions) == {"on-path": "1.0"}
    assert pkgs == {"on_path": ["on-path"]}
    assert nested_mapping == {"on_path.py": ("on-path", "1.0")}
    # ... and did not stick: the maps built afterwards are complete.
    assert set(mapping) == {"from_finder.py", "on_path.py"}
    assert dict(_p.get_distributions()) == {"from-finder": "1.0", "on-path": "1.0"}
    assert _p.get_package_distributions() == {"from_finder": ["from-finder"], "on_path": ["on-path"]}


def test_reads_after_first_use_are_cheap(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the first read has done the full check, reads only compare sys.path and
    the meta path, with no file system access; a sys.path change still updates
    the maps.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "first", "first")
    extra = _site_with_dist(tmp_path / "extra", "second", "second")
    monkeypatch.setattr(sys, "path", [str(site)])
    _prefetch_and_wait(_p)
    assert _p._package_for_root_module_mapping() == {"first.py": ("first", "1.0")}

    stats = []
    real_mtime = _p._mtime
    monkeypatch.setattr(_p, "_mtime", lambda entry: stats.append(entry) or real_mtime(entry))
    for _ in range(3):
        _p._package_for_root_module_mapping()
        _p.get_distributions()
    assert stats == []

    sys.path.append(str(extra))
    assert _p.get_module_distribution_versions("second") == ("second", "1.0")
    assert set(_p._package_for_root_module_mapping()) == {"first.py", "second.py"}
    assert dict(_p.get_distributions()) == {"first": "1.0", "second": "1.0"}


def test_readers_wait_for_the_background_prefetch(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader that arrives while the boot-time scan is pending waits for it, and
    the scan happens once.
    """
    import threading
    import time

    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "slow", "slow")
    monkeypatch.setattr(sys, "path", [str(site)])
    started = threading.Event()
    scans = []
    real_scan = _p.scan_distributions

    def slow_scan(entry, *args):
        scans.append(entry)
        started.set()
        time.sleep(0.3)
        return real_scan(entry, *args)

    monkeypatch.setattr(_p, "scan_distributions", slow_scan)

    _p.prefetch_distributions()
    assert started.wait(10)

    assert _p._package_for_root_module_mapping() == {"slow.py": ("slow", "1.0")}
    assert scans == [str(site)]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_fork_during_background_prefetch(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fork waits for the boot-time scan, so the child inherits it finished and
    reads the maps without scanning again.
    """
    import threading
    import time
    import warnings

    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "forked", "forked")
    monkeypatch.setattr(sys, "path", [str(site)])
    started = threading.Event()
    real_scan = _p.scan_distributions

    def slow_scan(entry, *args):
        started.set()
        time.sleep(0.3)
        return real_scan(entry, *args)

    monkeypatch.setattr(_p, "scan_distributions", slow_scan)

    _p.prefetch_distributions()
    assert started.wait(10)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        ok = _p._PREFETCH_THREAD is None and _p._INSTALLED is not None
        _p.scan_distributions = None  # any scan in the child would fail
        ok = ok and _p._package_for_root_module_mapping() == {"forked.py": ("forked", "1.0")}
        os._exit(0 if ok else 1)
    deadline = time.monotonic() + 30
    while (status := os.waitpid(pid, os.WNOHANG))[0] == 0:
        if time.monotonic() > deadline:
            os.kill(pid, 9)
            pytest.fail("fork child hung")
        time.sleep(0.05)
    assert os.waitstatus_to_exitcode(status[1]) == 0


def test_queued_prefetch_does_not_strand_a_fork_child(reset_packages_caches) -> None:
    """A prefetch start queued during a fork never runs in the child; the child must
    not wait for it.
    """
    from ddtrace.internal import packages as _p

    _p._PREFETCH_THREAD = object()  # a start that is still queued
    _p._PREFETCH_DONE.clear()

    _p._reset_prefetch_after_fork()

    assert _p._PREFETCH_THREAD is None
    assert _p._PREFETCH_DONE.is_set()


def test_lookups_cached_inside_a_finder_query_are_dropped(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finder that looks up its own module while being queried gets an answer
    from incomplete records; that answer must not stay cached.
    """
    from ddtrace.internal import packages as _p

    custom = _site_with_dist(tmp_path / "custom", "from-finder", "from_finder")
    seen = []

    def look_up_own_module():
        if not seen:
            seen.append(_p.get_module_distribution_versions("from_finder"))

    finder = _DistFinder(custom / "from_finder-1.0.dist-info", on_find=look_up_own_module)
    monkeypatch.setattr(sys, "path", [])
    monkeypatch.setattr(sys, "meta_path", [finder, _p.PathFinder])
    _p.get_module_distribution_versions.cache_clear()

    _p._installed()

    assert seen == [None]
    assert _p.get_module_distribution_versions("from_finder") == ("from-finder", "1.0")


def test_lookup_caches_follow_snapshot_replacement(
    tmp_path: Path, reset_packages_caches, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File attribution cached from one snapshot must not outlive it, but must
    survive reads that keep the same snapshot.
    """
    from ddtrace.internal import packages as _p

    site = _site_with_dist(tmp_path / "site", "first", "first")
    vendor = _site_with_dist(tmp_path / "vendor", "vendored", "first")
    monkeypatch.setattr(sys, "path", [str(site)])
    _prefetch_and_wait(_p)
    _p.filename_to_package.cache_clear()

    _p.filename_to_package(str(site / "first.py"))
    assert _p.filename_to_package.cache_info().currsize == 1

    # Same snapshot: the cached answer stays.
    _p._installed()
    assert _p.filename_to_package.cache_info().currsize == 1

    # The application vendors its own copy ahead of site-packages.
    sys.path.insert(0, str(vendor))
    assert [r[0] for r in _p._installed_distributions()] == ["vendored", "first"]
    assert _p.filename_to_package.cache_info().currsize == 0
