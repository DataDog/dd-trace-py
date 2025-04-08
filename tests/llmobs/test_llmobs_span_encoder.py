import json
import pytest

import mock

import ddtrace
from ddtrace.llmobs._constants import AGENTLESS_SPAN_BASE_URL
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.llmobs._utils import _chat_completion_event
from tests.llmobs._utils import _chat_completion_event_with_unserializable_field
from tests.llmobs._utils import _completion_event


DATADOG_SITE = "datad0g.com"
API_KEY = "<test-key>"


@pytest.fixture
def llmobs_span_writer():
    agentless_url = "{}.{}".format(AGENTLESS_SPAN_BASE_URL, DATADOG_SITE)
    yield TestLLMObsSpanWriter(DATADOG_SITE, API_KEY, 1.0, 5.0, is_agentless=True, _agentless_url=agentless_url)


def test_encode_span(llmobs_span_writer, mock_writer_logs):
    span = _chat_completion_event()
    encoded_llm_events = llmobs_span_writer._encode([span], 1)
    decoded_llm_events = json.loads(encoded_llm_events)
    assert len(decoded_llm_events) == 1
    assert decoded_llm_events == [span]
    mock_writer_logs.debug.assert_called_once_with("encoded %d LLMObs %s events to be sent", 1, "span")


def test_encode_multiple_spans(llmobs_span_writer, mock_writer_logs):
    trace = [_chat_completion_event(), _completion_event()]
    encoded_llm_events = llmobs_span_writer._encode(trace, 2)
    decoded_llm_events = json.loads(encoded_llm_events)
    assert len(decoded_llm_events) == 2
    assert decoded_llm_events == trace
    mock_writer_logs.debug.assert_called_once_with("encoded %d LLMObs %s events to be sent", 2, "span")


def test_encode_span_with_unserializable_fields(llmobs_span_writer):
    span = _chat_completion_event_with_unserializable_field()
    encoded_llm_events = llmobs_span_writer._encode([span], 1)
    decoded_llm_events = json.loads(encoded_llm_events)
    assert len(decoded_llm_events) == 1
    decoded_llm_span = decoded_llm_events[0]
    assert decoded_llm_span["meta"]["metadata"]["unserializable"] is not None
    assert "<object object at 0x" in decoded_llm_span["meta"]["metadata"]["unserializable"]
