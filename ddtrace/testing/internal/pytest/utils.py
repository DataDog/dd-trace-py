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
_CACHED_TEST_REF_ATTR = "_ddtrace_test_ref"
_CACHED_HAS_CUSTOM_NAME_HOOKS_ATTR = "_ddtrace_has_custom_name_hooks"


def _hook_has_impls(hookcaller: t.Any) -> bool:
    get_hookimpls = getattr(hookcaller, "get_hookimpls", None)
    if get_hookimpls is None:
        return True
    return bool(get_hookimpls())


def _has_custom_name_hooks(config: pytest.Config) -> bool:
    cached = getattr(config, _CACHED_HAS_CUSTOM_NAME_HOOKS_ATTR, None)
    if cached is not None:
        return t.cast(bool, cached)

    hook = config.hook
    has_hooks = any(
        (
            _hook_has_impls(hook.pytest_ddtrace_get_item_module_name),
            _hook_has_impls(hook.pytest_ddtrace_get_item_suite_name),
            _hook_has_impls(hook.pytest_ddtrace_get_item_test_name),
        )
    )
    setattr(config, _CACHED_HAS_CUSTOM_NAME_HOOKS_ATTR, has_hooks)
    return has_hooks


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
    cached_test_ref = getattr(item, _CACHED_TEST_REF_ATTR, None)
    if cached_test_ref is not None:
        return t.cast(TestRef, cached_test_ref)

    default_module, default_suite, default_test = nodeid_to_names(item.nodeid)

    if _has_custom_name_hooks(item.config):
        custom_module = item.config.hook.pytest_ddtrace_get_item_module_name(item=item)
        custom_suite = item.config.hook.pytest_ddtrace_get_item_suite_name(item=item)
        custom_test = item.config.hook.pytest_ddtrace_get_item_test_name(item=item)
    else:
        custom_module = custom_suite = custom_test = None

    module_ref = ModuleRef(custom_module or default_module)
    suite_ref = SuiteRef(module_ref, custom_suite or default_suite)
    test_ref = TestRef(suite_ref, custom_test or default_test)
    setattr(item, _CACHED_TEST_REF_ATTR, test_ref)

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
