import re
import typing as t

import pytest

from ddtrace.testing.internal.constants import EMPTY_NAME
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


_NODEID_REGEX = re.compile("^(((?P<module>.*)/)?(?P<suite>[^/]*?))::(?P<name>.*?)$")


def nodeid_to_names(nodeid: str) -> t.Tuple[str, str, str]:
    matches = _NODEID_REGEX.match(nodeid)

    if matches:
        module = matches.group("module") or EMPTY_NAME
        suite = matches.group("suite") or EMPTY_NAME
        test = matches.group("name") or EMPTY_NAME

    else:
        # Fallback to considering the whole nodeid as the test name.
        module = EMPTY_NAME
        suite = EMPTY_NAME
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
