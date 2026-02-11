"""APM tests for llama_index integration.

IMPORTANT: This file has the EXACT test structure required. Do NOT:
- Delete the span assertions
- Replace with weak assertions like 'assert spans is not None'
- Add @pytest.mark.skip decorators
- Delete tests

Keep the assertion structure. Adjust values based on actual library behavior.
"""
import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch


@pytest.fixture(autouse=True)
def patch_llama_index():
    """Automatically patch llama_index for all tests."""
    patch()
    yield
    unpatch()

def test_query_success(test_spans):
    """Test query creates span on success."""
    import llama_index

    client = llama_index

    result = client.query(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.query", f"Expected span name 'llama_index.query', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_query_error(test_spans):
    """Test query creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.query(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.query"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_aquery_success(test_spans):
    """Test aquery creates span on success."""
    import llama_index

    client = llama_index

    result = client.aquery(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aquery", f"Expected span name 'llama_index.aquery', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_aquery_error(test_spans):
    """Test aquery creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.aquery(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aquery"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_retrieve_success(test_spans):
    """Test retrieve creates span on success."""
    import llama_index

    client = llama_index

    result = client.retrieve(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.retrieve", f"Expected span name 'llama_index.retrieve', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_retrieve_error(test_spans):
    """Test retrieve creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.retrieve(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.retrieve"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_aretrieve_success(test_spans):
    """Test aretrieve creates span on success."""
    import llama_index

    client = llama_index

    result = client.aretrieve(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aretrieve", f"Expected span name 'llama_index.aretrieve', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_aretrieve_error(test_spans):
    """Test aretrieve creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.aretrieve(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aretrieve"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_chat_success(test_spans):
    """Test chat creates span on success."""
    import llama_index

    client = llama_index

    result = client.chat(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.chat", f"Expected span name 'llama_index.chat', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_chat_error(test_spans):
    """Test chat creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.chat(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.chat"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_complete_success(test_spans):
    """Test complete creates span on success."""
    import llama_index

    client = llama_index

    result = client.complete(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.complete", f"Expected span name 'llama_index.complete', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_complete_error(test_spans):
    """Test complete creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.complete(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.complete"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_stream_chat_success(test_spans):
    """Test stream_chat creates span on success."""
    import llama_index

    client = llama_index

    result = client.stream_chat(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.stream_chat", f"Expected span name 'llama_index.stream_chat', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_stream_chat_error(test_spans):
    """Test stream_chat creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.stream_chat(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.stream_chat"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_stream_complete_success(test_spans):
    """Test stream_complete creates span on success."""
    import llama_index

    client = llama_index

    result = client.stream_complete(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.stream_complete", f"Expected span name 'llama_index.stream_complete', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_stream_complete_error(test_spans):
    """Test stream_complete creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.stream_complete(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.stream_complete"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_achat_success(test_spans):
    """Test achat creates span on success."""
    import llama_index

    client = llama_index

    result = client.achat(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.achat", f"Expected span name 'llama_index.achat', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_achat_error(test_spans):
    """Test achat creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.achat(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.achat"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_acomplete_success(test_spans):
    """Test acomplete creates span on success."""
    import llama_index

    client = llama_index

    result = client.acomplete(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.acomplete", f"Expected span name 'llama_index.acomplete', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_acomplete_error(test_spans):
    """Test acomplete creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.acomplete(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.acomplete"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_astream_chat_success(test_spans):
    """Test astream_chat creates span on success."""
    import llama_index

    client = llama_index

    result = client.astream_chat(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.astream_chat", f"Expected span name 'llama_index.astream_chat', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_astream_chat_error(test_spans):
    """Test astream_chat creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.astream_chat(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.astream_chat"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_astream_complete_success(test_spans):
    """Test astream_complete creates span on success."""
    import llama_index

    client = llama_index

    result = client.astream_complete(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.astream_complete", f"Expected span name 'llama_index.astream_complete', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_astream_complete_error(test_spans):
    """Test astream_complete creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.astream_complete(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.astream_complete"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_get_query_embedding_success(test_spans):
    """Test get_query_embedding creates span on success."""
    import llama_index

    client = llama_index

    result = client.get_query_embedding(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.get_query_embedding", f"Expected span name 'llama_index.get_query_embedding', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_get_query_embedding_error(test_spans):
    """Test get_query_embedding creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.get_query_embedding(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.get_query_embedding"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_aget_query_embedding_success(test_spans):
    """Test aget_query_embedding creates span on success."""
    import llama_index

    client = llama_index

    result = client.aget_query_embedding(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aget_query_embedding", f"Expected span name 'llama_index.aget_query_embedding', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_aget_query_embedding_error(test_spans):
    """Test aget_query_embedding creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.aget_query_embedding(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aget_query_embedding"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_get_text_embedding_batch_success(test_spans):
    """Test get_text_embedding_batch creates span on success."""
    import llama_index

    client = llama_index

    result = client.get_text_embedding_batch(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.get_text_embedding_batch", f"Expected span name 'llama_index.get_text_embedding_batch', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_get_text_embedding_batch_error(test_spans):
    """Test get_text_embedding_batch creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.get_text_embedding_batch(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.get_text_embedding_batch"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_aget_text_embedding_batch_success(test_spans):
    """Test aget_text_embedding_batch creates span on success."""
    import llama_index

    client = llama_index

    result = client.aget_text_embedding_batch(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aget_text_embedding_batch", f"Expected span name 'llama_index.aget_text_embedding_batch', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_aget_text_embedding_batch_error(test_spans):
    """Test aget_text_embedding_batch creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.aget_text_embedding_batch(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.aget_text_embedding_batch"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_take_step_success(test_spans):
    """Test take_step creates span on success."""
    import llama_index

    client = llama_index

    result = client.take_step(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.take_step", f"Expected span name 'llama_index.take_step', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_take_step_error(test_spans):
    """Test take_step creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.take_step(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.take_step"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None


def test_run_agent_step_success(test_spans):
    """Test run_agent_step creates span on success."""
    import llama_index

    client = llama_index

    result = client.run_agent_step(
        # AGENT: Add required arguments
    )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.run_agent_step", f"Expected span name 'llama_index.run_agent_step', got '{span.name}'"
    assert span.service == "llama_index", f"Expected service 'llama_index', got '{span.service}'"
    assert span.error == 0, f"Expected no error, got error={span.error}"


def test_run_agent_step_error(test_spans):
    """Test run_agent_step creates span with error on failure."""
    import llama_index

    client = llama_index

    with pytest.raises(Exception):  # AGENT: Replace with actual error type
        client.run_agent_step(
            # AGENT: Add arguments that cause an error
        )

    spans = test_spans.pop_traces()
    assert len(spans) == 1, f"Expected 1 trace, got {len(spans)}"
    assert len(spans[0]) >= 1, f"Expected at least 1 span, got {len(spans[0])}"

    span = spans[0][0]
    assert span.name == "llama_index.run_agent_step"
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None
