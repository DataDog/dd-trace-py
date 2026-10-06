import pytest

from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.utils.cache import cached


@cached()
def _cached_sentinel():
    pass


@pytest.fixture
def packages():
    from ddtrace.internal import packages as _p

    yield _p

    # Clear caches
    from ddtrace.internal.packages import reset_package_root_mapping_cache

    reset_package_root_mapping_cache()

    for f in _p.__dict__.values():
        try:
            if f.__code__ is _cached_sentinel.__code__:
                f.cache_clear()
        except AttributeError:
            pass


def test_get_distributions():
    """use pkg_resources to validate package names and versions returned by get_distributions()"""
    import pkg_resources

    pkg_resources_ws = {pkg.project_name.lower() for pkg in pkg_resources.working_set}

    importlib_pkgs = set()
    for name, version in get_distributions().items():
        assert version
        # The package name in typing_extensions-4.x.x.dist-info/METADATA is set to `typing_extensions`
        # this is inconsistent with the package name found in pkg_resources. The block below corrects this.
        # The correct package name is typing-extensions.
        # The issue exists in pkgutil-resolve-name package.
        if name == "typing_extensions" and "typing-extensions" in pkg_resources_ws:
            importlib_pkgs.add("typing-extensions")
        elif name == "pkgutil_resolve_name" and "pkgutil-resolve-name" in pkg_resources_ws:
            importlib_pkgs.add("pkgutil-resolve-name")
        elif name == "importlib_metadata" and "importlib-metadata" in pkg_resources_ws:
            importlib_pkgs.add("importlib-metadata")
        elif name == "importlib-metadata" and "importlib_metadata" in pkg_resources_ws:
            importlib_pkgs.add("importlib_metadata")
        elif name == "importlib-resources" and "importlib_resources" in pkg_resources_ws:
            importlib_pkgs.add("importlib_resources")
        elif name == "importlib_resources" and "importlib-resources" in pkg_resources_ws:
            importlib_pkgs.add("importlib-resources")
        elif name == "openfeature-sdk" and "openfeature_sdk" in pkg_resources_ws:
            importlib_pkgs.add("openfeature_sdk")
        elif name == "openfeature_sdk" and "openfeature-sdk" in pkg_resources_ws:
            importlib_pkgs.add("openfeature-sdk")
        else:
            importlib_pkgs.add(name)
        # Fix for last zope namespace changes
        for sub in ["interface", "event"]:
            if f"zope-{sub}" in pkg_resources_ws and f"zope.{sub}" in importlib_pkgs:
                pkg_resources_ws.discard(f"zope-{sub}")
                importlib_pkgs.discard(f"zope.{sub}")
        # Fix for jaraco namespace packages (same normalization issue as zope)
        for sub in ["context", "functools", "classes", "text"]:
            if f"jaraco-{sub}" in pkg_resources_ws and f"jaraco.{sub}" in importlib_pkgs:
                pkg_resources_ws.discard(f"jaraco-{sub}")
                importlib_pkgs.discard(f"jaraco.{sub}")

    # assert that pkg_resources and importlib.metadata return the same packages
    assert pkg_resources_ws == importlib_pkgs


def test_filename_to_package(packages) -> None:
    package = packages.filename_to_package(packages.__file__)
    assert package is None or package.name == "ddtrace"
    package = packages.filename_to_package(pytest.__file__)
    assert package.name == "pytest"

    import httpretty

    package = packages.filename_to_package(httpretty.__file__)
    assert package.name == "httpretty"

    try:
        package = packages.filename_to_package("You may be wondering how I got here even though I am not a file.")
    except Exception:
        pytest.fail("filename_to_package should not raise an exception when given a non-file path")


def test_lookup_does_not_block_while_the_scan_runs(packages) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()
    real = packages._package_for_root_module_mapping

    def slow_scan():
        started.set()
        assert release.wait(2)
        return {}

    packages.reset_package_root_mapping_cache()
    packages._package_for_root_module_mapping = slow_scan
    try:
        packages.schedule_package_mapping()
        assert started.wait(2)
        assert packages.filename_to_package(packages.__file__) is None
        thread = packages._mapping_build_thread
        assert isinstance(thread, packages._forksafe_threads.Thread)
        from ddtrace.internal._threads import periodic_threads

        assert any(registered is thread for registered in periodic_threads.values())
    finally:
        release.set()
        if packages._mapping_build_thread is not None:
            packages._mapping_build_thread.join(2)
        packages._package_for_root_module_mapping = real
        packages.reset_package_root_mapping_cache()


def test_scan_discards_a_map_when_sys_path_changes_during_the_scan(packages) -> None:
    import sys

    marker = "/added-during-package-scan"
    calls = {"n": 0}
    real = packages._package_for_root_module_mapping

    def grow_path_once():
        calls["n"] += 1
        if calls["n"] == 1 and marker not in sys.path:
            sys.path.append(marker)
        return {}

    packages.reset_package_root_mapping_cache()
    packages._package_for_root_module_mapping = grow_path_once
    try:
        packages._run_mapping_build()
        thread = packages._mapping_build_thread
        if thread is not None:
            thread.join(2)
        assert calls["n"] == 2
        assert packages._mapping_built_for_path == tuple(sys.path)
        assert marker in packages._mapping_built_for_path
    finally:
        if marker in sys.path:
            sys.path.remove(marker)
        if packages._mapping_build_thread is not None:
            packages._mapping_build_thread.join(2)
        packages._package_for_root_module_mapping = real
        packages.reset_package_root_mapping_cache()


def test_schedule_does_not_start_a_worker_during_fork(packages, monkeypatch) -> None:
    monkeypatch.setattr(packages._forksafe_threads, "_forking", True)
    packages.reset_package_root_mapping_cache()
    try:
        packages.schedule_package_mapping()
        assert packages._mapping_build_thread is None
    finally:
        packages.reset_package_root_mapping_cache()


def test_fork_hook_restarts_an_unfinished_scan(packages) -> None:
    import threading

    class DeadThread(threading.Thread):
        def is_alive(self) -> bool:
            return False

    packages.reset_package_root_mapping_cache()
    packages._mapping_build_thread = DeadThread(name="ddtrace-package-mapping")
    try:
        packages._after_fork_reschedule_package_mapping()
        thread = packages._mapping_build_thread
        assert thread is not None
        thread.join(10)
        stored = packages._mapping_callonce_result()
        assert stored is not None and stored[1] is None
    finally:
        if packages._mapping_build_thread is not None:
            packages._mapping_build_thread.join(2)
        packages.reset_package_root_mapping_cache()


def test_fork_hook_keeps_a_finished_map(packages) -> None:
    import sys
    import threading

    class DeadThread(threading.Thread):
        def is_alive(self) -> bool:
            return True

    packages.reset_package_root_mapping_cache()
    packages._package_for_root_module_mapping.__wrapped__.__callonce_result__ = ({}, None)
    packages._mapping_built_for_path = tuple(sys.path)
    packages._mapping_build_thread = DeadThread(name="ddtrace-package-mapping")
    try:
        packages._after_fork_reschedule_package_mapping()
        assert packages._mapping_build_thread is None
        assert packages._mapping_callonce_result() == ({}, None)
    finally:
        packages.reset_package_root_mapping_cache()


def test_in_progress_scan_does_not_cache_user_code_misses(packages) -> None:
    from pathlib import Path
    import threading

    class AliveThread(threading.Thread):
        def is_alive(self) -> bool:
            return True

    path = str(packages.__file__)
    packages.reset_package_root_mapping_cache()
    packages._mapping_build_thread = AliveThread(name="ddtrace-package-mapping")
    try:
        third_party_before = packages._is_third_party_cached.cache_info().currsize
        user_code_before = packages._is_user_code_str_cached.cache_info().currsize
        assert packages.is_third_party(Path(path)) is False
        packages.is_user_code(path)
        assert packages._is_third_party_cached.cache_info().currsize == third_party_before
        assert packages._is_user_code_str_cached.cache_info().currsize == user_code_before
    finally:
        packages._mapping_build_thread = None
        packages.reset_package_root_mapping_cache()


def test_cached_lookup_drops_a_value_stored_as_the_scan_finishes(packages) -> None:
    from functools import lru_cache
    import sys

    calls = {"n": 0}

    @lru_cache(maxsize=8)
    def cached(arg):
        calls["n"] += 1
        if calls["n"] == 1:
            packages._mapping_generation += 1
            return "stale"
        return "fresh"

    packages.reset_package_root_mapping_cache()
    packages._package_for_root_module_mapping.__wrapped__.__callonce_result__ = ({}, None)
    packages._mapping_built_for_path = tuple(sys.path)
    try:
        assert packages._cached_lookup(cached, "x") == "fresh"
        assert calls["n"] == 2
        assert cached.cache_info().currsize == 1
    finally:
        packages.reset_package_root_mapping_cache()


def test_third_party_packages():
    assert 4000 < len(_third_party_packages()) < 5000

    assert "requests" in _third_party_packages()
    assert "nota3rdparty" not in _third_party_packages()


@pytest.mark.subprocess(
    env={
        "DD_THIRD_PARTY_DETECTION_INCLUDES": "myfancypackage,myotherfancypackage",
        "DD_THIRD_PARTY_DETECTION_EXCLUDES": "requests",
    }
)
def test_third_party_packages_excludes_includes():
    from ddtrace.internal.packages import _third_party_packages

    assert {"myfancypackage", "myotherfancypackage"} < _third_party_packages()
    assert "requests" not in _third_party_packages()


def test_third_party_packages_symlinks(tmp_path):
    """
    Test that a symlink doesn't break our logic of detecting user code.
    """
    import os

    from ddtrace.internal.packages import is_user_code

    # Use pathlib for more pythonic directory creation
    actual_path = tmp_path / "site-packages" / "ddtrace"
    runfiles_path = tmp_path / "test.runfiles" / "site-packages" / "ddtrace"

    # Create directories using pathlib (more pythonic)
    actual_path.mkdir(parents=True)
    runfiles_path.mkdir(parents=True)

    # Assert that the runfiles path is considered user code when symlinked.
    code_file = actual_path / "test.py"
    code_file.write_bytes(b"#")

    symlink_file = runfiles_path / "test.py"
    os.symlink(code_file, symlink_file)

    assert not is_user_code(code_file)
    # Symlinks with `.runfiles` in the path should not be considered user code.
    from pathlib import Path

    p = Path(symlink_file)
    p2 = Path(symlink_file).resolve()
    print(symlink_file, p, p2)

    assert not is_user_code(symlink_file)

    code_file_2 = runfiles_path / "test2.py"
    code_file_2.write_bytes(b"#")

    assert not is_user_code(code_file_2)
