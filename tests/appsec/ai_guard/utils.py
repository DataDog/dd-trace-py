import contextlib
import random
import string
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from unittest.mock import Mock

import ddtrace
from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.settings.asm import ai_guard_config
from tests.utils import DummyTracer


def random_string(length: int) -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(length))


def find_ai_guard_span(tracer: DummyTracer) -> Span:
    spans = tracer.get_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == AI_GUARD.RESOURCE_TYPE
    return span


def assert_ai_guard_span(tracer: DummyTracer, messages: List[Message], tags: Dict[str, Any]) -> None:
    span = find_ai_guard_span(tracer)
    for tag, value in tags.items():
        assert tag in span.get_tags(), f"Missing {tag} from spans tags"
        assert span.get_tag(tag) == value, f"Wrong value {span.get_tag(tag)}, expected {value}"
    struct = span._get_struct_tag(AI_GUARD.TAG)
    assert struct["messages"] == messages


def mock_evaluate_response(action: str, reason: str = "", block: bool = True) -> Mock:
    mock_response = Mock()
    mock_response.status = 200
    mock_response.get_json.return_value = {
        "data": {"attributes": {"action": action, "reason": reason, "is_blocking_enabled": block}}
    }
    return mock_response


def assert_mock_execute_request_call(
    mock_execute_request,
    ai_guard_client: AIGuardClient,
    messages: List[Message],
    meta: Optional[Dict[str, Any]] = None,
):
    expected_attributes = {"messages": messages, "meta": meta or {"service": config.service, "env": config.env}}

    expected_payload = {"data": {"attributes": expected_attributes}}

    mock_execute_request.assert_called_once_with(
        f"{ai_guard_client._endpoint}/evaluate",
        expected_payload,
    )


@contextlib.contextmanager
def override_ai_guard_config(values):
    """
    Temporarily override an ai_guard configuration:

        >>> with self.override_ai_guard_config(dict(name=value,...)):
            # Your test
    """
    # List of global variables we allow overriding
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
