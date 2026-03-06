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
        name = dist.metadata["Name"]
        version = dist.metadata["Version"]
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


def test_distributions_files():
    """Test that dist.files returns correct file list matching stdlib behavior."""
    import importlib.metadata as stdlib_im

    from ddtrace.internal.native._native import distributions as _distributions

    # Build a mapping of distribution name -> set of file paths from stdlib
    stdlib_files = {}
    for dist in stdlib_im.distributions():
        name = dist.metadata["Name"]
        if name and dist.files:
            stdlib_files[name.lower()] = {str(f) for f in dist.files}

    # Build same mapping from our implementation
    our_files = {}
    for dist in _distributions():
        name = dist.name
        if name and dist.files:
            our_files[name.lower()] = {str(f) for f in dist.files}

    # Verify files match for distributions that have them
    for name in stdlib_files:
        if name in our_files:
            assert stdlib_files[name] == our_files[name], (
                f"Files mismatch for {name}:\n"
                f"  Only in stdlib: {sorted(stdlib_files[name] - our_files[name])[:5]}\n"
                f"  Only in ours: {sorted(our_files[name] - stdlib_files[name])[:5]}"
            )


def test_distributions_read_text():
    """Test that dist.read_text() works correctly."""
    import importlib.metadata as stdlib_im

    from ddtrace.internal.native._native import distributions as _distributions

    # Verify our implementation returns the same content as stdlib for top_level.txt
    for dist in _distributions():
        name = dist.name
        if name:
            our_top_level = dist.read_text("top_level.txt")
            # Find the corresponding stdlib distribution at the same path
            matching_stdlib_content = None
            for stdlib_dist in stdlib_im.distributions():
                if stdlib_dist.metadata["Name"] == name and str(getattr(stdlib_dist, "_path", "")) == str(dist.path):
                    matching_stdlib_content = stdlib_dist.read_text("top_level.txt")
                    break

            # Only assert if we found a matching stdlib distribution
            if matching_stdlib_content is not None or our_top_level is not None:
                if not (matching_stdlib_content is None and our_top_level is None):
                    assert our_top_level == matching_stdlib_content, (
                        f"top_level.txt mismatch for {name} at {dist.path}:\n"
                        f"  stdlib: {repr(matching_stdlib_content)}\n"
                        f"  ours: {repr(our_top_level)}"
                    )

    # Test reading non-existent file returns None
    for dist in _distributions():
        result = dist.read_text("this_file_does_not_exist_12345.txt")
        assert result is None, f"Expected None for non-existent file, got {repr(result)}"
        break  # Only need to test one distribution


def test_package_path_parts():
    """Test that PackagePath.parts returns correct tuple."""
    import importlib.metadata as stdlib_im

    from ddtrace.internal.native._native import distributions as _distributions

    # Get a distribution with files from stdlib
    stdlib_parts = {}
    for dist in stdlib_im.distributions():
        name = dist.metadata["Name"]
        if name and dist.files:
            # Store first few file parts for comparison
            stdlib_parts[name.lower()] = {str(f): f.parts for f in list(dist.files)[:5]}
            if stdlib_parts[name.lower()]:
                break

    # Verify our implementation returns matching parts
    for dist in _distributions():
        name = dist.name
        if name and name.lower() in stdlib_parts and dist.files:
            for f in dist.files:
                f_str = str(f)
                if f_str in stdlib_parts[name.lower()]:
                    assert tuple(f.parts) == stdlib_parts[name.lower()][f_str], (
                        f"Parts mismatch for {f_str}:\n  stdlib: {stdlib_parts[name.lower()][f_str]}\n  ours: {f.parts}"
                    )
            break


def test_package_path_locate():
    """Test that PackagePath.locate() returns a valid path."""
    from pathlib import Path

    from ddtrace.internal.native._native import distributions as _distributions

    # Find a distribution with files and verify locate() returns a path
    for dist in _distributions():
        if dist.files:
            for f in dist.files[:3]:  # Test first 3 files
                located = f.locate()
                # locate() should return a path (string or Path-like)
                assert located is not None, f"locate() returned None for {f}"
                # The path should be absolute or relative to dist-info
                located_path = Path(str(located))
                # We just verify it's a valid path string, not that the file exists
                # (some files in RECORD may be deleted or not installed)
                assert len(str(located_path)) > 0, f"locate() returned empty path for {f}"
            break


def test_distribution_metadata_keys():
    """Test that Distribution.metadata has the expected keys for Rust implementation."""
    from ddtrace.internal.native._native import distributions as _distributions

    for dist in _distributions():
        metadata = dist.metadata
        # Verify required keys exist (both cases as documented)
        assert "name" in metadata or "Name" in metadata, f"Missing name key in metadata for {dist}"
        assert "version" in metadata or "Version" in metadata, f"Missing version key in metadata for {dist}"

        # Verify the values match the direct properties
        if "name" in metadata:
            assert metadata["name"] == dist.name, f"metadata['name'] != dist.name for {dist}"
        if "Name" in metadata:
            assert metadata["Name"] == dist.name, f"metadata['Name'] != dist.name for {dist}"
        if "version" in metadata:
            assert metadata["version"] == dist.version, f"metadata['version'] != dist.version for {dist}"
        if "Version" in metadata:
            assert metadata["Version"] == dist.version, f"metadata['Version'] != dist.version for {dist}"
        break  # Only need to test one distribution


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
