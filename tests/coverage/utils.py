from pathlib import Path


def _get_relpath_str(rootpath: Path, path: Path):
    try:
        return str(path.relative_to(rootpath))
    except ValueError:
        return str(path)


def _get_relpath_dict(rootpath: str, dict_to_update: dict):
    """Expects a dictionary of path strings to anything and returns an identical dictionary with the keys changed to
    relative path strings
    """
    return {
        _get_relpath_str(Path(rootpath), Path(path)): set(lines.to_sorted_list())
        for path, lines in dict_to_update.items()
    }


def _get_lines_set_from_coverage_lines():
    """Convenience function to extract the set of lines from a CoverageLines object"""


def assert_coverage_matches(actual_coverage, expected_coverage, file_level_mode, context_name=""):
    """
    Assert that coverage matches expectations based on the coverage mode.

    In file-level mode:
        - Compares the set of files (keys) present in coverage
        - Verifies each file has some coverage data (len > 0)

    In line-level mode:
        - Compares actual line numbers against expected line numbers

    Args:
        actual_coverage: Dict mapping file paths to sets of line numbers
        expected_coverage: In file-level mode, set of expected file paths.
                          In line-level mode, dict mapping file paths to sets of expected lines.
        file_level_mode: Boolean indicating whether running in file-level mode
        context_name: Optional name for error messages
    """
    context_prefix = f"{context_name}: " if context_name else ""

    if file_level_mode:
        # File-level mode: compare keys only
        if not isinstance(expected_coverage, set):
            # If expected is a dict, extract keys
            expected_files = set(expected_coverage.keys())
        else:
            expected_files = expected_coverage

        actual_files = set(actual_coverage.keys())

        assert (
            actual_files == expected_files
        ), f"{context_prefix}File coverage mismatch: expected={expected_files} vs actual={actual_files}"

        # Verify each file has some coverage
        for file_path in expected_files:
            assert len(actual_coverage[file_path]) > 0, f"{context_prefix}File {file_path} has no coverage data"
    else:
        # Line-level mode: compare actual line numbers
        assert (
            actual_coverage == expected_coverage
        ), f"{context_prefix}Line coverage mismatch: expected={expected_coverage} vs actual={actual_coverage}"
