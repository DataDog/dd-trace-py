from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "original_module, canonical_module, names",
    [
        (
            "ddtrace.contrib._events.exception",
            "ddtrace.internal.core.exception",
            ("ContextExceptionEvent",),
        ),
        (
            "ddtrace.contrib._events.http",
            "ddtrace.internal.core.http",
            (
                "HttpBaseEvent",
                "HttpRequestBaseEvent",
            ),
        ),
        (
            "ddtrace.contrib._events.web_framework",
            "ddtrace._trace.web_framework",
            ("WebFrameworkEvents", "WebFrameworkRequestEvent"),
        ),
    ],
)
def test_existing_event_imports_preserve_type_identity(original_module, canonical_module, names):
    original = import_module(original_module)
    canonical = import_module(canonical_module)

    for name in names:
        assert getattr(original, name) is getattr(canonical, name)
