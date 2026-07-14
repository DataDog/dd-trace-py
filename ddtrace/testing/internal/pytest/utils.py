from __future__ import annotations

import json
import logging
import re
import typing as t

import pytest

from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


log = logging.getLogger(__name__)

_NODEID_REGEX = re.compile("^(((?P<module>.*)/)?(?P<suite>[^/]*?))::(?P<name>.*?)$")


def nodeid_to_names(nodeid: str) -> tuple[str, str, str]:
    matches = _NODEID_REGEX.match(nodeid)

    if matches:
        module_raw = matches.group("module") or ""
        module = module_raw.replace("/", ".") if module_raw else ""
        suite = matches.group("suite") or ""
        test = matches.group("name") or ""

    else:
        # Fallback to considering the whole nodeid as the test name.
        module = "unknown_module"
        suite = "unknown_suite"
        test = nodeid

    return module, suite, test


def item_to_test_ref(item: pytest.Item) -> TestRef:
    custom_module = item.config.hook.pytest_ddtrace_get_item_module_name(item=item)
    custom_suite = item.config.hook.pytest_ddtrace_get_item_suite_name(item=item)
    custom_test = item.config.hook.pytest_ddtrace_get_item_test_name(item=item)

    default_module, default_suite, default_test = nodeid_to_names(item.nodeid)

    module_ref = ModuleRef(custom_module or default_module)
    suite_ref = SuiteRef(module_ref, custom_suite or default_suite)
    test_ref = TestRef(suite_ref, custom_test or default_test)

    return test_ref


def _encode_test_parameter(parameter: t.Any) -> str:
    param_repr = repr(parameter)
    # if the representation includes an id() we'll remove it
    # because it isn't constant across executions
    return re.sub(r" at 0[xX][0-9a-fA-F]+", "", param_repr)


def _get_test_parameters_json(item: pytest.Item) -> t.Optional[str]:
    callspec: t.Optional[pytest.python.CallSpec2] = getattr(item, "callspec", None)

    if callspec is None:
        return None

    parameters: dict[str, dict[str, str]] = {"arguments": {}, "metadata": {}}
    for param_name, param_val in item.callspec.params.items():
        try:
            parameters["arguments"][param_name] = _encode_test_parameter(param_val)
        except Exception:
            parameters["arguments"][param_name] = "Could not encode"
            log.warning("Failed to encode %r", param_name, exc_info=True)

    try:
        return json.dumps(parameters, sort_keys=True)
    except TypeError:
        log.warning("Failed to serialize parameters for test %s", item, exc_info=True)
        return None
