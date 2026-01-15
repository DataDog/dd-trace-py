import re
import typing as t

import pytest

from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


_NODEID_REGEX = re.compile("^(((?P<module>.*)/)?(?P<suite>[^/]*?))::(?P<name>.*?)$")


def nodeid_to_names(nodeid: str) -> t.Tuple[str, str, str]:
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

    # DEBUG: Log for the problematic test
    if "inject_span" in item.nodeid:
        import logging

        log = logging.getLogger(__name__)
        # log.debug(f"*** NEW PLUGIN item_to_test_ref CALLED: nodeid='{item.nodeid}', name='{item.name}' ***")
        # log.debug(f"*** NEW PLUGIN FINAL test_name: '{custom_test or default_test}' ***")
        log.debug("DEBUG NEW PLUGIN item_to_test_ref:")
        log.debug("  item.nodeid: %s", item.nodeid)
        log.debug("  item.name: %s", item.name)
        log.debug("  custom_module: %s", custom_module)
        log.debug("  custom_suite: %s", custom_suite)
        log.debug("  custom_test: %s", custom_test)
        log.debug("  default_module: %s", default_module)
        log.debug("  default_suite: %s", default_suite)
        log.debug("  default_test: %s", default_test)
        log.debug("  final module: %s", custom_module or default_module)
        log.debug("  final suite: %s", custom_suite or default_suite)
        log.debug("  final test: %s", custom_test or default_test)

    module_ref = ModuleRef(custom_module or default_module)
    suite_ref = SuiteRef(module_ref, custom_suite or default_suite)
    test_ref = TestRef(suite_ref, custom_test or default_test)

    # DEBUG: Log the final TestRef for inject_span test
    if "inject_span" in item.nodeid:
        print(f"*** NEW PLUGIN FINAL TestRef: {test_ref.suite.module.name}/{test_ref.suite.name}::{test_ref.name} ***")
        log.debug("NEW PLUGIN FINAL TestRef: %s/%s::%s", test_ref.suite.module.name, test_ref.suite.name, test_ref.name)

    return test_ref
