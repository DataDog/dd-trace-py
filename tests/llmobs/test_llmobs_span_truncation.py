import json

from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR
from ddtrace.llmobs._constants import EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._writer import _truncate_span_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


def test_truncates_oversized_span_values():
    span_event = _truncate_span_event(_oversized_workflow_event())
    assert len(json.dumps(span_event)) < EVP_EVENT_SIZE_LIMIT
    assert span_event["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]


def test_truncates_oversized_span_messages():
    span_event = _truncate_span_event(_oversized_llm_event())
    assert len(json.dumps(span_event)) < EVP_EVENT_SIZE_LIMIT
    assert span_event["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]


def test_truncates_oversized_span_documents():
    span_event = _truncate_span_event(_oversized_retrieval_event())
    assert len(json.dumps(span_event)) < EVP_EVENT_SIZE_LIMIT
    assert span_event["collection_errors"] == [DROPPED_IO_COLLECTION_ERROR]
