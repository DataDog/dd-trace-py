import json

from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR
from ddtrace.llmobs._integrations.utils import LLMOBS_IMAGE_INLINE_MAX_BYTES
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs._writer import _truncate_span_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


def _llm_event_with_image(encoded_len):
    event = _oversized_llm_event()
    event["meta"]["input"] = {
        "messages": [
            {
                "role": "user",
                "content": "what is in this image?",
                "image_parts": [{"mime_type": "image/png", "content": "A" * encoded_len}],
            }
        ]
    }
    event["meta"]["output"] = {"messages": [{"role": "assistant", "content": "a dog"}]}
    return event


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


def test_inline_image_at_guard_cap_stays_under_event_limit():
    """What the integrations' per-image guard buys: an image captured at the cap leaves the event small
    enough that LLMObsSpanWriter.enqueue never truncates, so the prompt text and model response ship.
    """
    # enqueue truncates on `len(safe_json(event)) >= limit`, so measure the same way it does.
    assert len(safe_json(_llm_event_with_image(LLMOBS_IMAGE_INLINE_MAX_BYTES))) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_unguarded_inline_image_would_lose_input_and_output():
    """The failure the guard exists to prevent: an over-cap image pushes the event past the limit, and
    truncation blanks the input AND the output -- not just the image.
    """
    event = _llm_event_with_image(LLMOBS_IMAGE_INLINE_MAX_BYTES * 2)
    assert len(safe_json(event)) >= DEFAULT_EVP_EVENT_SIZE_LIMIT
    truncated = _truncate_span_event(event)
    assert "messages" not in truncated["meta"]["input"]
    assert "messages" not in truncated["meta"]["output"]
    assert truncated["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]
