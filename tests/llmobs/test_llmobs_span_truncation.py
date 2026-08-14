import json

from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR
from ddtrace.llmobs._constants import DROPPED_VALUE_TEXT
from ddtrace.llmobs._integrations.utils import _capture_inline_image
from ddtrace.llmobs._integrations.utils import _inline_image_budget
from ddtrace.llmobs._writer import _truncate_span_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


def test_truncates_oversized_span_values():
    span_event = _truncate_span_event(_oversized_workflow_event())
    assert len(json.dumps(span_event)) < DEFAULT_EVP_EVENT_SIZE_LIMIT
    assert span_event["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]


def test_truncates_oversized_span_messages():
    span_event = _truncate_span_event(_oversized_llm_event())
    assert len(json.dumps(span_event)) < DEFAULT_EVP_EVENT_SIZE_LIMIT
    assert span_event["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]


def test_truncates_oversized_span_documents():
    span_event = _truncate_span_event(_oversized_retrieval_event())
    assert len(json.dumps(span_event)) < DEFAULT_EVP_EVENT_SIZE_LIMIT
    assert span_event["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]


def _llm_event_with_image(image_part):
    """A minimal LLM span event whose input carries one captured image."""
    return {
        "span_id": "1",
        "trace_id": "1",
        "name": "openai.request",
        "meta": {
            "span": {"kind": "llm"},
            "input": {"messages": [{"role": "user", "content": "what is this?", "image_parts": [image_part]}]},
            "output": {"messages": [{"role": "assistant", "content": "a photo"}]},
        },
        "metrics": {},
    }


def test_at_cap_image_event_stays_under_the_event_limit():
    """An image the guard admits must leave the whole event under the limit, not just the field.

    This is the claim the size guard exists to make; the openai E2E test cannot check it because
    that fixture mocks the writer.
    """
    part, marker = _capture_inline_image("data:image/png;base64," + "A" * _inline_image_budget())
    assert marker is None and part is not None  # exactly at cap: admitted

    event = _llm_event_with_image(part)
    assert len(json.dumps(event)) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_over_cap_image_never_reaches_the_event():
    """An over-cap image degrades to a marker, so the event never needs truncating at all."""
    part, marker = _capture_inline_image("data:image/png;base64," + "A" * (_inline_image_budget() + 1))
    assert part is None and marker == "[image omitted: too large]"

    # The message the integration would build instead: text plus marker, no image_parts.
    event = _llm_event_with_image({"mime_type": "image/png", "content": ""})
    event["meta"]["input"]["messages"][0] = {"role": "user", "content": "what is this?\n" + marker}
    truncated = _truncate_span_event(dict(event))
    assert len(json.dumps(event)) < DEFAULT_EVP_EVENT_SIZE_LIMIT
    assert truncated["meta"]["input"]["value"] == DROPPED_VALUE_TEXT  # only if forced, not organically
