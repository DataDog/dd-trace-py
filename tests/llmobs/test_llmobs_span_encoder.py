import json

import mock

import ddtrace
from ddtrace.llmobs._writer import LLMObsSpanEncoder
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _chat_completion_event_with_unserializable_field
from tests.llmobs._utils import _completion_event


def test_encode_span(mock_writer_logs):
    encoder = LLMObsSpanEncoder(0, 0)
    span = _chat_completion_event()
    encoder.put([span])
    encoded_llm_events, n_spans = encoder.encode()

    expected_llm_events = [
        {
            "_dd.stage": "raw",
            "_dd.tracer_version": ddtrace.__version__,
            "event_type": "span",
            "spans": [span],
        }
    ]

    assert n_spans == 1
    decoded_llm_events = json.loads(encoded_llm_events)
    assert decoded_llm_events == expected_llm_events
    mock_writer_logs.debug.assert_called_once_with("encode %d LLMObs span events to be sent", 1)


def test_encode_multiple_spans(mock_writer_logs):
    encoder = LLMObsSpanEncoder(0, 0)
    trace = [_chat_completion_event(), _completion_event()]
    encoder.put(trace)
    encoded_llm_events, n_spans = encoder.encode()

    expected_llm_events = [
        {
            "_dd.stage": "raw",
            "_dd.tracer_version": ddtrace.__version__,
            "event_type": "span",
            "spans": [trace[0]],
        },
        {
            "_dd.stage": "raw",
            "_dd.tracer_version": ddtrace.__version__,
            "event_type": "span",
            "spans": [trace[1]],
        },
    ]

    assert n_spans == 2
    decoded_llm_events = json.loads(encoded_llm_events)
    assert decoded_llm_events == expected_llm_events
    mock_writer_logs.debug.assert_called_once_with("encode %d LLMObs span events to be sent", 2)


def test_encode_span_with_unserializable_fields():
    encoder = LLMObsSpanEncoder(0, 0)
    span = _chat_completion_event_with_unserializable_field()
    encoder.put([span])
    encoded_llm_events, n_spans = encoder.encode()

    expected_llm_events = [
        {
            "_dd.stage": "raw",
            "_dd.tracer_version": ddtrace.__version__,
            "event_type": "span",
            "spans": [mock.ANY],
        }
    ]

    assert n_spans == 1
    decoded_llm_events = json.loads(encoded_llm_events)
    assert decoded_llm_events == expected_llm_events
    decoded_llm_span = decoded_llm_events[0]["spans"][0]
    assert decoded_llm_span["meta"]["metadata"]["unserializable"] is not None
    assert "<object object at 0x" in decoded_llm_span["meta"]["metadata"]["unserializable"]
