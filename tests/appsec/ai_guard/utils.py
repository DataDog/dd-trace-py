import contextlib
import random
import string
from typing import Any
from typing import Optional
from unittest.mock import Mock

import ddtrace
from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.internal.settings.asm import ai_guard_config
from tests.utils import TracerSpanContainer


def random_string(length: int) -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(length))


def find_ai_guard_span(test_spans: TracerSpanContainer) -> Span:
    spans = test_spans.spans
    assert len(spans) == 1
    span = spans[0]
    assert span.name == AI_GUARD.RESOURCE_TYPE
    return span


def assert_ai_guard_span(
    test_spans: TracerSpanContainer,
    tags: dict[str, Any],
    meta_struct: dict[str, Any],
) -> None:
    span = find_ai_guard_span(test_spans)
    for tag, value in tags.items():
        assert tag in span.get_tags(), f"Missing {tag} from spans tags"
        assert span.get_tag(tag) == value, f"Wrong value {span.get_tag(tag)}, expected {value}"
    struct = span._get_struct_tag(AI_GUARD.TAG)
    for meta, value in meta_struct.items():
        assert meta in struct.keys(), f"Missing {meta} from meta_struct keys"
        assert struct[meta] == value, f"Wrong value {struct[meta]}, expected {value}"


def mock_evaluate_response(
    action: str,
    reason: str = "",
    tags: list[str] = None,
    block: bool = True,
    sds_findings: list = None,
) -> Mock:
    mock_response = Mock()
    mock_response.status = 200
    attributes = {
        "action": action,
        "reason": reason,
        "tags": tags if tags else [],
        "is_blocking_enabled": block,
    }
    if sds_findings is not None:
        attributes["sds_findings"] = sds_findings
    mock_response.get_json.return_value = {"data": {"attributes": attributes}}
    return mock_response


def assert_mock_execute_request_call(
    mock_execute_request,
    ai_guard_client: AIGuardClient,
    messages: list[Message],
    meta: Optional[dict[str, Any]] = None,
    endpoint: Optional[str] = None,
):
    expected_attributes = {"messages": messages, "meta": meta or {"service": config.service, "env": config.env}}

    expected_payload = {"data": {"attributes": expected_attributes}}

    expected_endpoint = endpoint if endpoint else ai_guard_client._endpoint
    mock_execute_request.assert_called_once_with(
        f"{expected_endpoint}/evaluate",
        expected_payload,
    )


@contextlib.contextmanager
def override_ai_guard_config(values):
    """
    Temporarily override an ai_guard configuration:

        >>> with self.override_ai_guard_config(dict(name=value,...)):
            # Your test
    """
    # list of global variables we allow overriding
    global_config_keys = [
        "_dd_api_key",
        "_dd_app_key",
    ]

    ai_guard_config_keys = ai_guard_config._ai_guard_config_keys

    # Grab the current values of all keys
    originals = dict((key, getattr(ddtrace.config, key)) for key in global_config_keys)
    ai_guard_originals = dict((key, getattr(ai_guard_config, key)) for key in ai_guard_config_keys)

    # Override from the passed in keys
    for key, value in values.items():
        if key in global_config_keys:
            setattr(ddtrace.config, key, value)
    # rebuild ai guard config from env vars and global config
    for key, value in values.items():
        if key in ai_guard_config_keys:
            setattr(ai_guard_config, key, value)

    try:
        yield
    finally:
        # Reset all to their original values
        for key, value in originals.items():
            setattr(ddtrace.config, key, value)

        ai_guard_config.reset()
        for key, value in ai_guard_originals.items():
            setattr(ai_guard_config, key, value)

        ddtrace.config._reset()
