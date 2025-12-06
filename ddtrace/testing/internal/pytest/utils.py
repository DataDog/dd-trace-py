import re

from ddtrace.testing.internal.constants import EMPTY_NAME
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


_NODEID_REGEX = re.compile("^(((?P<module>.*)/)?(?P<suite>[^/]*?))::(?P<name>.*?)$")


def nodeid_to_test_ref(nodeid: str) -> TestRef:
    matches = _NODEID_REGEX.match(nodeid)

    if matches:
        module_ref = ModuleRef(matches.group("module") or EMPTY_NAME)
        suite_ref = SuiteRef(module_ref, matches.group("suite") or EMPTY_NAME)
        test_ref = TestRef(suite_ref, matches.group("name"))
        return test_ref

    else:
        # Fallback to considering the whole nodeid as the test name.
        module_ref = ModuleRef(EMPTY_NAME)
        suite_ref = SuiteRef(module_ref, EMPTY_NAME)
        test_ref = TestRef(suite_ref, nodeid)
        return test_ref
