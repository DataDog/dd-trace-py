from dataclasses import dataclass
import json
import os
import re
import typing as t

import pytest

from ddtrace.contrib.pytest.constants import ITR_MIN_SUPPORTED_VERSION
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_NODEID_REGEX = re.compile("^((?P<module>.*)/(?P<suite>[^/]*?))::(?P<name>.*?)$")


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


def _get_session_id(session: pytest.Session):
    """The session name is constant as multiple test sessions are not currently supported"""
    return CISessionId("pytest_session")


def _get_names_from_item(item: pytest.Item) -> TestNames:
    """Gets an item's module, suite, and test names by leveraging the plugin hooks"""

    matches = re.match(_NODEID_REGEX, item.nodeid)
    if not matches:
        return TestNames(module="", suite="", test="")

    return TestNames(
        module=matches.group("module").replace("/", "."), suite=matches.group("suite"), test=matches.group("name")
    )


def _get_test_id_from_item(item: pytest.Item) -> CITestId:
    """Converts an item to a CITestId, which recursively includes the parent IDs

    NOTE: it is mandatory that the session, module, suite, and test IDs for a given test and parameters combination
    be stable across test runs.
    """

    module_name = item.config.hook.pytest_ddtrace_get_item_module_name(item=item)
    suite_name = item.config.hook.pytest_ddtrace_get_item_suite_name(item=item)
    test_name = item.config.hook.pytest_ddtrace_get_item_test_name(item=item)

    session_id = _get_session_id(item.session)
    module_id = CIModuleId(session_id, module_name)
    suite_id = CISuiteId(module_id, suite_name)

    # Test parameters are part of the test ID
    parameters_json: t.Optional[str] = None
    if getattr(item, "callspec", None):
        parameters = {"arguments": {}, "metadata": {}}  # type: Dict[str, Dict[str, str]]
        for param_name, param_val in item.callspec.params.items():
            try:
                parameters["arguments"][param_name] = _encode_test_parameter(param_val)
            except Exception:
                parameters["arguments"][param_name] = "Could not encode"
                log.warning("Failed to encode %r", param_name, exc_info=True)
        parameters_json = json.dumps(parameters)

    test_id = CITestId(suite_id, test_name, parameters_json)

    print(test_id)

    return test_id


def _get_session_command(session: pytest.Session):
    """Extract and re-create pytest session command from pytest config."""
    command = "pytest"
    if getattr(session.config, "invocation_params", None):
        command += " {}".format(" ".join(session.config.invocation_params.args))
    if os.environ.get("PYTEST_ADDOPTS"):
        command += " {}".format(os.environ.get("PYTEST_ADDOPTS"))
    return command


def _get_pytest_version_tuple() -> t.Tuple[int, ...]:
    if hasattr(pytest, "version_tuple"):
        return pytest.version_tuple
    return tuple(map(int, pytest.__version__.split(".")))


def _is_pytest_8_or_later() -> bool:
    return _get_pytest_version_tuple() >= (8, 0, 0)


def _pytest_version_supports_itr() -> bool:
    return _get_pytest_version_tuple() >= ITR_MIN_SUPPORTED_VERSION
