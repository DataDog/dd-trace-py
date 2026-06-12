from __future__ import annotations

import json
import logging
from pathlib import Path
import typing as t

import pytest

from ddtrace.internal.settings import env
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_DISCOVERY_FILE
from ddtrace.testing.internal.git import get_workspace_path
from ddtrace.testing.internal.pytest.utils import _get_test_parameters_json
from ddtrace.testing.internal.pytest.utils import item_to_test_ref
from ddtrace.testing.internal.utils import asbool


log = logging.getLogger(__name__)

_DEFAULT_OUTPUT_PATH = ".testoptimization/tests-discovery/tests.json"


def is_discovery_mode_enabled() -> bool:
    return asbool(env.get(DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED))


def _get_output_path() -> Path:
    return Path(env.get(DD_TEST_OPTIMIZATION_DISCOVERY_FILE, _DEFAULT_OUTPUT_PATH))


def _is_item_skipped(item: pytest.Item) -> bool:
    """Return True if the item will definitely be skipped at execution time.

    Handles pytest.mark.skip (unconditional) and pytest.mark.skipif with
    non-string conditions. String conditions are not evaluated (would require
    exec in the test module's namespace) so those tests are conservatively
    included.
    """
    if item.get_closest_marker("skip") is not None:
        return True
    for marker in item.iter_markers("skipif"):
        condition = marker.args[0] if marker.args else marker.kwargs.get("condition")
        if condition is None or isinstance(condition, str):
            # String conditions require eval in the test module's namespace, which
            # we can't do safely at collection time. None conditions arise when
            # @pytest.mark.skipif is used with only a reason= kwarg and no positional
            # condition — pytest 9 accepts this and skips the test at runtime, but
            # does not signal it during collection.
            # Conservatively include in both cases rather than risk hiding a test.
            continue
        if condition:
            return True
    return False


def _get_suite_source_file(item: pytest.Item, workspace_path: t.Optional[Path]) -> str:
    item_path = Path(item.path if hasattr(item, "path") else getattr(item, "fspath", "")).absolute()
    if workspace_path is not None:
        try:
            return item_path.relative_to(workspace_path).as_posix()
        except ValueError:
            pass
    return str(item_path)


@pytest.hookimpl(tryfirst=True)  # type: ignore[misc]
def pytest_collection_finish(session: pytest.Session) -> None:
    if not is_discovery_mode_enabled():
        return

    workspace_path: t.Optional[Path] = None
    try:
        workspace_path = get_workspace_path()
    except Exception:
        log.debug("Could not determine workspace path for test discovery", exc_info=True)

    output_path = _get_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for item in session.items:
            if _is_item_skipped(item):
                continue

            test_ref = item_to_test_ref(item)
            module = test_ref.suite.module.name
            suite = test_ref.suite.name
            name = test_ref.name
            parameters = _get_test_parameters_json(item)
            suite_source_file = _get_suite_source_file(item, workspace_path)

            test_info: dict[str, t.Any] = {
                "name": name,
                "suite": suite,
                "module": module,
                "parameters": parameters,
                "suiteSourceFile": suite_source_file,
            }
            f.write(json.dumps(test_info) + "\n")

    log.info("Test discovery complete: wrote tests to %s", output_path)
    pytest.exit("Test discovery complete", returncode=0)
