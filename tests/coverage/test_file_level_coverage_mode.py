import typing as t
from unittest.mock import Mock

import pytest

from ddtrace.internal.coverage import util
from ddtrace.internal.coverage.util import is_file_level_coverage_enabled


@pytest.mark.parametrize(
    "version_info,env_value,expected",
    [
        ((3, 11, 9), None, False),
        ((3, 11, 9), "false", False),
        ((3, 11, 9), "true", True),
        ((3, 12, 0), None, True),
        ((3, 12, 0), "true", True),
        ((3, 12, 0), "false", False),
        ((3, 14, 2), None, True),
        ((3, 16, 0), None, True),
    ],
)
def test_is_file_level_coverage_enabled(
    monkeypatch: pytest.MonkeyPatch,
    version_info: tuple[int, int, int],
    env_value: t.Optional[str],
    expected: bool,
) -> None:
    monkeypatch.setattr(util, "sys", Mock(version_info=version_info))
    if env_value is None:
        monkeypatch.delenv("_DD_COVERAGE_FILE_LEVEL", raising=False)
    else:
        monkeypatch.setenv("_DD_COVERAGE_FILE_LEVEL", env_value)

    assert is_file_level_coverage_enabled() is expected


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": None})
def test_collector_default_mode_matches_instrumentation():
    # Callers that do not pass file_level_coverage, such as multiprocessing children, must collect in the same mode
    # as the instrumentation.
    import os
    from pathlib import Path
    import sys

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    install(include_paths=[Path(os.getcwd() + "/tests/coverage/included_path/")])

    if sys.version_info >= (3, 12):
        from ddtrace.internal.coverage.instrumentation_py3_12 import _USE_FILE_LEVEL_COVERAGE

        assert _USE_FILE_LEVEL_COVERAGE is True
        assert ModuleCodeCollector.file_level_coverage_enabled() is True
    else:
        assert ModuleCodeCollector.file_level_coverage_enabled() is False
