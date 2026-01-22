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

    try:
        del _p._package_for_root_module_mapping.__closure__[0].cell_contents.__callonce_result__
    except AttributeError:
        pass

    for f in _p.__dict__.values():
        try:
            if f.__code__ is _cached_sentinel.__code__:
                f.cache_clear()
        except AttributeError:
            pass


def test_get_distributions():
    """use stdlib importlib.metadata to validate package names and versions returned by get_distributions()"""
    import importlib.metadata as stdlib_im

    # Get packages from stdlib importlib.metadata (Python fallback implementation)
    stdlib_pkgs = {}
    for dist in stdlib_im.distributions():
        name = dist.metadata['Name']
        version = dist.metadata['Version']
        if name and version:
            stdlib_pkgs[name.lower()] = version

    # Get packages from our implementation (Rust-optimized or Python fallback)
    our_pkgs = get_distributions()

    # Verify all packages match
    assert set(stdlib_pkgs.keys()) == set(our_pkgs.keys()), (
        f"Package mismatch:\n"
        f"  Only in stdlib: {sorted(set(stdlib_pkgs.keys()) - set(our_pkgs.keys()))}\n"
        f"  Only in ours: {sorted(set(our_pkgs.keys()) - set(stdlib_pkgs.keys()))}"
    )

    # Verify versions match
    for name in stdlib_pkgs:
        assert our_pkgs[name] == stdlib_pkgs[name], (
            f"Version mismatch for {name}: stdlib={stdlib_pkgs[name]}, ours={our_pkgs[name]}"
        )


def test_filename_to_package(packages):
    # type: (...) -> None
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
