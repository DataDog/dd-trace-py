import json

from ddtrace.internal.evp_proxy.constants import DEFAULT_EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR
from ddtrace.llmobs._constants import DROPPED_VALUE_TEXT
from ddtrace.llmobs._integrations.utils import LLMOBS_IMAGE_INLINE_MAX_BYTES
from ddtrace.llmobs._integrations.utils import _capture_inline_image
from ddtrace.llmobs._integrations.utils import _inline_image_budget
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


def _llm_event_with_image_part(image_part):
    """An LLM span event carrying one already-captured ImagePart.

    Distinct from _llm_event_with_image, which takes a byte length: these cases need the real part
    that _capture_inline_image returned, not a synthetic payload of a given size.
    """
    event = _oversized_llm_event()
    event["meta"]["input"] = {"messages": [{"role": "user", "content": "what is this?", "image_parts": [image_part]}]}
    event["meta"]["output"] = {"messages": [{"role": "assistant", "content": "a photo"}]}
    return event


def test_at_cap_image_from_data_url_stays_under_the_event_limit():
    """An image the OpenAI data-URL path admits leaves the whole event under the limit.

    Covers the config-derived budget rather than the fixed constant: exercises _capture_inline_image
    end to end, which the openai E2E test cannot check because that fixture mocks the writer.
    """
    part, marker = _capture_inline_image("data:image/png;base64," + "A" * _inline_image_budget())
    assert marker is None and part is not None  # exactly at cap: admitted

    assert len(safe_json(_llm_event_with_image_part(part))) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_over_cap_image_from_data_url_never_reaches_the_event():
    """An over-budget data URL degrades to a marker, so the event never needs truncating at all."""
    part, marker = _capture_inline_image("data:image/png;base64," + "A" * (_inline_image_budget() + 1))
    assert part is None and marker == "[image omitted: too large]"

    # The message the integration builds instead: text plus marker, no image_parts at all.
    event = _oversized_llm_event()
    event["meta"]["input"] = {"messages": [{"role": "user", "content": "what is this?\n" + marker}]}
    event["meta"]["output"] = {"messages": [{"role": "assistant", "content": "a photo"}]}
    assert len(safe_json(event)) < DEFAULT_EVP_EVENT_SIZE_LIMIT
    # Truncation still blanks input when forced -- it just is never reached for this event.
    assert _truncate_span_event(event)["meta"]["input"]["value"] == DROPPED_VALUE_TEXT


def test_inline_image_at_guard_cap_stays_under_event_limit():
    """What the integrations' per-image guard buys: an image captured at the cap leaves the event small
    enough that LLMObsSpanWriter.enqueue never truncates, so the prompt text and model response ship.
    """
    # enqueue truncates on `len(safe_json(event)) >= limit`, so measure the same way it does.
    assert len(safe_json(_llm_event_with_image(LLMOBS_IMAGE_INLINE_MAX_BYTES))) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_unguarded_inline_image_loses_only_the_input():
    """An over-cap image pushes the event past the limit; truncation blanks the input that holds it
    and keeps the small model response.
    """
    event = _llm_event_with_image(LLMOBS_IMAGE_INLINE_MAX_BYTES * 2)
    assert len(safe_json(event)) >= DEFAULT_EVP_EVENT_SIZE_LIMIT
    truncated = _truncate_span_event(event)
    assert truncated["meta"]["input"] == {"value": DROPPED_VALUE_TEXT}
    assert truncated["meta"]["output"] == {"messages": [{"role": "assistant", "content": "a dog"}]}
    assert truncated["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]
    assert len(safe_json(truncated)) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_truncation_drops_largest_field_first_and_stops_when_under_limit():
    event = _oversized_workflow_event()
    event["meta"]["output"] = {"value": "B" * 3_000_000}
    truncated = _truncate_span_event(event)
    assert truncated["meta"]["output"] == {"value": DROPPED_VALUE_TEXT}
    assert truncated["meta"]["input"] == {"value": "A" * 2_600_000}
    assert len(safe_json(truncated)) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_truncation_keeps_experiment_expected_output_and_metadata_when_io_is_enough():
    event = _oversized_workflow_event()
    event["meta"]["expected_output"] = {"answer": "42"}
    event["meta"]["metadata"] = {"dataset_record_id": "r1"}
    truncated = _truncate_span_event(event)
    assert truncated["meta"]["expected_output"] == {"answer": "42"}
    assert truncated["meta"]["metadata"] == {"dataset_record_id": "r1"}
    assert len(safe_json(truncated)) < DEFAULT_EVP_EVENT_SIZE_LIMIT


def test_truncation_drops_expected_output_when_io_is_not_enough():
    event = _oversized_workflow_event()
    event["meta"]["input"] = {"value": "small"}
    event["meta"]["output"] = {"value": "small"}
    event["meta"]["expected_output"] = {"value": "C" * 5_100_000}
    truncated = _truncate_span_event(event)
    assert truncated["meta"]["expected_output"] == {"value": DROPPED_VALUE_TEXT}
    assert len(safe_json(truncated)) < DEFAULT_EVP_EVENT_SIZE_LIMIT
    assert truncated["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]


def test_truncation_ranks_fields_by_serialized_size():
    """Escaped characters count: an emoji-heavy input is bigger on the wire than a longer ASCII output."""
    event = _oversized_workflow_event()
    event["meta"]["input"] = "\U0001f600" * 450_000
    event["meta"]["output"] = "A" * 2_000_000
    assert len(safe_json(event)) >= DEFAULT_EVP_EVENT_SIZE_LIMIT
    truncated = _truncate_span_event(event)
    assert truncated["meta"]["input"] == {"value": DROPPED_VALUE_TEXT}
    assert truncated["meta"]["output"] == "A" * 2_000_000
