import json

from ddtrace.llmobs._constants import EVP_EVENT_SIZE_LIMIT
from ddtrace.llmobs._writer import _truncate_span_event
from tests.llmobs._utils import _oversized_llm_event
from tests.llmobs._utils import _oversized_retrieval_event
from tests.llmobs._utils import _oversized_workflow_event


def test_truncates_oversized_span_values():
    assert len(json.dumps(_truncate_span_event(_oversized_workflow_event()))) < EVP_EVENT_SIZE_LIMIT


def test_truncates_oversized_span_messages():
    assert len(json.dumps(_truncate_span_event(_oversized_llm_event()))) < EVP_EVENT_SIZE_LIMIT


def test_truncates_oversized_span_documents():
    assert len(json.dumps(_truncate_span_event(_oversized_retrieval_event()))) < EVP_EVENT_SIZE_LIMIT
