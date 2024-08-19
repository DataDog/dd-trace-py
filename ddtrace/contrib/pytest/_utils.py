from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import typing as t

import pytest

from ddtrace.contrib.pytest.constants import ITR_MIN_SUPPORTED_VERSION
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITest
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.ci_visibility.constants import ITR_UNSKIPPABLE_REASON
from ddtrace.internal.ci_visibility.utils import get_source_lines_for_test_method
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.inspection import undecorated


log = get_logger(__name__)

_NODEID_REGEX = re.compile("^(((?P<module>.*)/)?(?P<suite>[^/]*?))::(?P<name>.*?)$")

_USE_PLUGIN_V2 = asbool(os.environ.get("_DD_CIVISIBILITY_USE_PYTEST_V2", "false"))


@dataclass
class TestNames:
    module: str
    suite: str
    test: str


def _encode_test_parameter(parameter: t.Any) -> str:
    param_repr = repr(parameter)
    # if the representation includes an id() we'll remove it
    # because it isn't constant across executions
    return re.sub(r" at 0[xX][0-9a-fA-F]+", "", param_repr)


def _get_names_from_item(item: pytest.Item) -> TestNames:
    """Gets an item's module, suite, and test names by leveraging the plugin hooks"""

    matches = re.match(_NODEID_REGEX, item.nodeid)
    if not matches:
        return TestNames(module="unknown_module", suite="unknown_suite", test=item.name)

    module_name = (matches.group("module") or "").replace("/", ".")
    suite_name = matches.group("suite")
    test_name = matches.group("name")

    return TestNames(module=module_name, suite=suite_name, test=test_name)


@cached()
def _get_test_id_from_item(item: pytest.Item) -> CITestId:
    """Converts an item to a CITestId, which recursively includes the parent IDs

    NOTE: it is mandatory that the session, module, suite, and test IDs for a given test and parameters combination
    be stable across test runs.
    """

    module_name = item.config.hook.pytest_ddtrace_get_item_module_name(item=item)
    suite_name = item.config.hook.pytest_ddtrace_get_item_suite_name(item=item)
    test_name = item.config.hook.pytest_ddtrace_get_item_test_name(item=item)

    module_id = CIModuleId(module_name)
    suite_id = CISuiteId(module_id, suite_name)

    # Test parameters are part of the test ID
    parameters_json: t.Optional[str] = None
    if getattr(item, "callspec", None):
        parameters: t.Dict[str, t.Dict[str, str]] = {"arguments": {}, "metadata": {}}
        for param_name, param_val in item.callspec.params.items():
            try:
                parameters["arguments"][param_name] = _encode_test_parameter(param_val)
            except Exception:
                parameters["arguments"][param_name] = "Could not encode"
                log.warning("Failed to encode %r", param_name, exc_info=True)

        parameters_json = json.dumps(parameters)

    test_id = CITestId(suite_id, test_name, parameters_json)

    return test_id


def _get_module_path_from_item(item: pytest.Item) -> Path:
    return Path(item.nodeid.rpartition("/")[0]).absolute()


def _get_session_command(session: pytest.Session):
    """Extract and re-create pytest session command from pytest config."""
    command = "pytest"
    if getattr(session.config, "invocation_params", None):
        command += " {}".format(" ".join(session.config.invocation_params.args))
    if os.environ.get("PYTEST_ADDOPTS"):
        command += " {}".format(os.environ.get("PYTEST_ADDOPTS"))
    return command


def _get_source_file_info(item, item_path) -> t.Optional[CISourceFileInfo]:
    try:
        # TODO: don't depend on internal for source file info
        if hasattr(item, "_obj"):
            test_method_object = undecorated(item._obj, item.name, item_path)
            source_lines = get_source_lines_for_test_method(test_method_object)
            source_file_info = CISourceFileInfo(item_path, source_lines[0], source_lines[1])
        else:
            source_file_info = CISourceFileInfo(item_path, item.reportinfo()[1])
        return source_file_info
    except Exception:
        log.debug("Unable to get source file info for item %s (path %s)", item, item_path, exc_info=True)
        return None


def _get_pytest_version_tuple() -> t.Tuple[int, ...]:
    if hasattr(pytest, "version_tuple"):
        return pytest.version_tuple
    return tuple(map(int, pytest.__version__.split(".")))


def _is_pytest_8_or_later() -> bool:
    return _get_pytest_version_tuple() >= (8, 0, 0)


def _pytest_version_supports_itr() -> bool:
    return _get_pytest_version_tuple() >= ITR_MIN_SUPPORTED_VERSION


def _pytest_marked_to_skip(item: pytest.Item) -> bool:
    """Checks whether Pytest will skip an item"""
    if item.get_closest_marker("skip") is not None:
        return True

    return any(marker.args[0] for marker in item.iter_markers(name="skipif"))


def _is_test_unskippable(item: pytest.Item) -> bool:
    """Returns True if a test has a skipif marker with value false and reason ITR_UNSKIPPABLE_REASON"""
    return any(
        (marker.args[0] is False and marker.kwargs.get("reason") == ITR_UNSKIPPABLE_REASON)
        for marker in item.iter_markers(name="skipif")
    )


def _extract_span(item):
    """Extract span from `pytest.Item` instance."""
    if _USE_PLUGIN_V2:
        test_id = _get_test_id_from_item(item)
        return CITest.get_span(test_id)

    return getattr(item, "_datadog_span", None)
