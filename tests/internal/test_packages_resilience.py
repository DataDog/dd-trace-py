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
        for fn in (_p.get_distributions, _p._package_for_root_module_mapping):
            inner = getattr(fn, "__wrapped__", None) or (fn.__closure__[0].cell_contents if fn.__closure__ else None)
            if inner is not None and hasattr(inner, "__callonce_result__"):
                del inner.__callonce_result__
        _p._PACKAGE_DISTRIBUTIONS = None
        _p._reset_installed_distributions()
        _p._BAD_DISTS_WARNED.clear()

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


def test_prefetch_with_threads_matches_lazy_scan(reset_packages_caches) -> None:
    """The threaded boot-time scan must yield exactly what the lazy scan does,
    in the same order: the first distribution to claim a key wins.
    """
    from ddtrace.internal import packages as _p

    lazy = _p._installed_distributions()

    for threads in (2, 4, 8):
        _p._reset_installed_distributions()
        assert _p._installed_distributions(threads=threads) == lazy


def test_prefetch_never_raises(reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing scan at boot must not break the post-preload sequence."""
    from ddtrace.internal import packages as _p

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_p, "_distribution_records", boom)

    _p.prefetch_distributions()

    assert _p._INSTALLED_DISTRIBUTIONS is None


def test_concurrent_callers_scan_once(reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """The boot-time prefetch and a lazy caller must not both run the scan."""
    import threading
    import time

    from ddtrace.internal import packages as _p

    scans = []

    def slow_records(entries, segments, threads=1, warn=None):
        scans.append(threads)
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

    def records_with_problem(entries, segments, threads=1, warn=None):
        warn("/site/bad.dist-info", "not utf-8")
        return iter([])

    free = []
    monkeypatch.setattr(_p, "_distribution_records", records_with_problem)
    monkeypatch.setattr(
        _p, "_warn_bad_dist", lambda dist, exc: free.append(_lock_is_free(_p._INSTALLED_DISTRIBUTIONS_LOCK))
    )

    _p._installed_distributions()

    assert free == [True]


@pytest.mark.parametrize("auto", [False, True])
def test_prefetch_threads(auto: bool, reset_packages_caches, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under ddtrace.auto the scan must run on the calling thread alone;
    otherwise on as many threads as the process may use, up to the cap.
    """
    import os

    from ddtrace.internal import packages as _p

    requested = []

    def records(entries, segments, threads=1, warn=None):
        requested.append(threads)
        return iter([])

    monkeypatch.setattr(_p, "_distribution_records", records)
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(16)), raising=False)
    if auto:
        monkeypatch.setitem(sys.modules, "ddtrace.auto", object())
    else:
        monkeypatch.delitem(sys.modules, "ddtrace.auto", raising=False)

    _p.prefetch_distributions()

    assert requested == [1 if auto else _p._PREFETCH_MAX_THREADS]


def test_ddtrace_auto_prefetches_single_threaded(tmp_path: Path) -> None:
    """import ddtrace.auto must run the boot-time scan on the importing thread alone."""
    import subprocess

    script = tmp_path / "app.py"
    script.write_text(
        """
from ddtrace.internal import packages

requested = []
real = packages._installed_distributions
packages._installed_distributions = lambda threads=1: requested.append(threads) or real(threads)

import ddtrace.auto  # noqa: E402,F401

assert requested and requested[0] == 1, requested
"""
    )
    subprocess.run([sys.executable, str(script)], check=True, timeout=120)


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

    _p.prefetch_distributions()
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


@pytest.mark.skipif(
    sys.platform != "linux" or __import__("os").geteuid() == 0,
    reason="RLIMIT_NPROC only stops thread creation on Linux, and never for root",
)
def test_scan_survives_thread_spawn_failure() -> None:
    """When the OS refuses new threads, the threaded scan must still complete
    instead of panicking at interpreter startup.
    """
    import subprocess
    import sysconfig

    code = """
import resource, sys
from importlib.machinery import all_suffixes
from ddtrace.internal.native import scan_distributions
suffixes = sorted(all_suffixes(), key=len, reverse=True)
expected = scan_distributions(sys.argv[1], suffixes, 1)
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
assert scan_distributions(sys.argv[1], suffixes, 4) == expected
"""
    subprocess.run([sys.executable, "-c", code, sysconfig.get_path("purelib")], check=True, timeout=120)


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
