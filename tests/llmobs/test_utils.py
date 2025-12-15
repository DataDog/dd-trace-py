import mock
from pydantic import BaseModel
import pytest

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs._writer import LLMObsExportSpansClient
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.utils import Documents
from ddtrace.llmobs.utils import Messages


def test_messages_with_string():
    messages = Messages("hello")
    assert messages.messages == [{"content": "hello"}]


def test_messages_with_dict():
    messages = Messages({"content": "hello", "role": "user"})
    assert messages.messages == [{"content": "hello", "role": "user"}]


def test_messages_with_list_of_dicts():
    messages = Messages([{"content": "hello", "role": "user"}, {"content": "world", "role": "system"}])
    assert messages.messages == [{"content": "hello", "role": "user"}, {"content": "world", "role": "system"}]


def test_messages_with_incorrect_type():
    with pytest.raises(TypeError):
        Messages(123)
    with pytest.raises(TypeError):
        Messages(object())
    with pytest.raises(TypeError):
        Messages(None)


def test_messages_with_non_string_content():
    with pytest.raises(TypeError):
        Messages([{"content": 123}])
    with pytest.raises(TypeError):
        Messages([{"content": object()}])
    with pytest.raises(TypeError):
        Messages([{"content": None}])
    with pytest.raises(TypeError):
        Messages({"content": {"key": "value"}})


def test_messages_with_non_string_role():
    with pytest.raises(TypeError):
        Messages([{"content": "hello", "role": 123}])
    with pytest.raises(TypeError):
        Messages([{"content": "hello", "role": object()}])
    with pytest.raises(TypeError):
        Messages({"content": "hello", "role": {"key": "value"}})


def test_messages_with_no_role_is_ok():
    """Test that a message with no role is ok and returns a message with only content."""
    messages = Messages([{"content": "hello"}, {"content": "world"}])
    assert messages.messages == [{"content": "hello"}, {"content": "world"}]


def test_messages_with_tool_calls():
    """Test that messages can include tool calls."""
    messages = Messages(
        [
            {
                "content": "I'll help you with that calculation.",
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "calculator",
                        "arguments": {"operation": "add", "a": 5, "b": 3},
                        "tool_id": "call_123",
                        "type": "function",
                    }
                ],
            }
        ]
    )
    expected = [
        {
            "content": "I'll help you with that calculation.",
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "calculator",
                    "arguments": {"operation": "add", "a": 5, "b": 3},
                    "tool_id": "call_123",
                    "type": "function",
                }
            ],
        }
    ]
    assert messages.messages == expected


def test_messages_with_tool_results():
    """Test that messages can include tool results."""
    messages = Messages(
        [
            {
                "content": "",
                "role": "tool",
                "tool_results": [
                    {"name": "calculator", "result": "8", "tool_id": "call_123", "type": "function_result"}
                ],
            }
        ]
    )
    expected = [
        {
            "content": "",
            "role": "tool",
            "tool_results": [{"name": "calculator", "result": "8", "tool_id": "call_123", "type": "function_result"}],
        }
    ]
    assert messages.messages == expected


def test_messages_with_tool_calls_minimal():
    """Test tool calls with only required fields."""
    messages = Messages(
        [
            {
                "content": "Using calculator",
                "role": "assistant",
                "tool_calls": [{"name": "calculator", "arguments": {"x": 10}}],
            }
        ]
    )
    expected = [
        {
            "content": "Using calculator",
            "role": "assistant",
            "tool_calls": [{"name": "calculator", "arguments": {"x": 10}}],
        }
    ]
    assert messages.messages == expected


def test_messages_with_tool_results_minimal():
    """Test tool results with only required fields."""
    messages = Messages([{"content": "", "role": "tool", "tool_results": [{"result": "Success"}]}])
    expected = [{"content": "", "role": "tool", "tool_results": [{"result": "Success"}]}]
    assert messages.messages == expected


def test_messages_with_both_tool_calls_and_results():
    """Test that a message can have both tool calls and tool results"""
    messages = Messages(
        [
            {
                "content": "Processing...",
                "role": "assistant",
                "tool_calls": [{"name": "calculator", "arguments": {"x": 5}}],
                "tool_results": [{"result": "10"}],
            }
        ]
    )
    expected = [
        {
            "content": "Processing...",
            "role": "assistant",
            "tool_calls": [{"name": "calculator", "arguments": {"x": 5}}],
            "tool_results": [{"result": "10"}],
        }
    ]
    assert messages.messages == expected


def test_messages_tool_calls_missing_required_fields():
    """Test that tool_calls raise errors when required fields are missing."""
    # Missing name field
    with pytest.raises(TypeError, match="ToolCall name must be a non-empty string"):
        Messages([{"content": "test", "tool_calls": [{"arguments": {"x": 5}}]}])

    # Missing arguments field
    with pytest.raises(TypeError, match="ToolCall arguments must be a dictionary"):
        Messages([{"content": "test", "tool_calls": [{"name": "calculator"}]}])

    # Empty name field
    with pytest.raises(TypeError, match="ToolCall name must be a non-empty string"):
        Messages([{"content": "test", "tool_calls": [{"name": "", "arguments": {"x": 5}}]}])

    # Invalid arguments type
    with pytest.raises(TypeError, match="ToolCall arguments must be a dictionary"):
        Messages([{"content": "test", "tool_calls": [{"name": "calculator", "arguments": "invalid"}]}])


def test_messages_tool_results_missing_required_fields():
    """Test that tool_results raise errors when required fields are missing."""
    # Missing result field
    with pytest.raises(TypeError, match="ToolResult result must be a string"):
        Messages([{"content": "test", "tool_results": [{"name": "calculator"}]}])

    # Invalid result type
    with pytest.raises(TypeError, match="ToolResult result must be a string"):
        Messages([{"content": "test", "tool_results": [{"result": 123}]}])


def test_documents_with_string():
    documents = Documents("hello")
    assert documents.documents == [{"text": "hello"}]


def test_documents_with_dict():
    documents = Documents({"text": "hello", "name": "doc1", "id": "123", "score": 0.5})
    assert len(documents.documents) == 1
    assert documents.documents == [{"text": "hello", "name": "doc1", "id": "123", "score": 0.5}]


def test_documents_with_list_of_dicts():
    documents = Documents([{"text": "hello", "name": "doc1", "id": "123", "score": 0.5}, {"text": "world"}])
    assert len(documents.documents) == 2
    assert documents.documents[0] == {"text": "hello", "name": "doc1", "id": "123", "score": 0.5}
    assert documents.documents[1] == {"text": "world"}


def test_documents_with_incorrect_type():
    with pytest.raises(TypeError):
        Documents(123)
    with pytest.raises(TypeError):
        Documents(object())
    with pytest.raises(TypeError):
        Documents(None)


def test_documents_dictionary_no_text_value():
    with pytest.raises(TypeError):
        Documents([{"text": None}])
    with pytest.raises(TypeError):
        Documents([{"name": "doc1", "id": "123", "score": 0.5}])


def test_documents_dictionary_with_incorrect_value_types():
    with pytest.raises(TypeError):
        Documents([{"text": 123}])
    with pytest.raises(TypeError):
        Documents([{"text": [1, 2, 3]}])
    with pytest.raises(TypeError):
        Documents([{"text": "hello", "id": 123}])
    with pytest.raises(TypeError):
        Documents({"text": "hello", "name": {"key": "value"}})
    with pytest.raises(TypeError):
        Documents([{"text": "hello", "score": "123"}])


def test_json_serialize_primitives():
    assert safe_json(123) == "123"
    assert safe_json(123.45) == "123.45"
    assert safe_json("hello world") == "hello world"
    assert safe_json(True) == "true"
    assert safe_json(None) == "null"


def test_json_serialize_list():
    assert safe_json([1, 2, 3]) == "[1, 2, 3]"
    assert safe_json(["hello", "world"]) == '["hello", "world"]'


def test_json_serialize_dict():
    assert safe_json({"name": "hello world", "age": 123}) == '{"name": "hello world", "age": 123}'


def test_json_serialize_pydantic_model():
    class Model(BaseModel):
        name: str
        age: int

    pydantic_model = Model(name="hello world", age=123)
    encoded_model = safe_json(pydantic_model)
    assert encoded_model == '{"name": "hello world", "age": 123}'


def test_json_serialize_pydantic_model_with_complex_field():
    class Metadata(BaseModel):
        key: str
        value: str

    class Model(BaseModel):
        name: str
        metadata: Metadata

    pydantic_model = Model(name="hello world", metadata=Metadata(key="goodbye", value="cruel world"))
    encoded_model = safe_json(pydantic_model)
    assert encoded_model == '{"name": "hello world", "metadata": {"key": "goodbye", "value": "cruel world"}}'


def test_json_serialize_class_with_repr():
    class Class:
        pass

    encoded_obj = safe_json(Class())
    assert '"<tests.llmobs.test_utils.test_json_serialize_class_with_repr.<locals>.Class object at 0x' in encoded_obj


def test_json_serialize_class_with_str():
    class Class:
        def __str__(self):
            return "Class"

    class_with_str = Class()
    encoded_obj = safe_json(class_with_str)
    assert encoded_obj == '"Class"'


def test_export_spans_client_build_url_options():
    export_spans_client = LLMObsExportSpansClient(
        api_key="test-api-key",
        app_key="test-app-key",
        site="test-site",
    )
    url_options = export_spans_client._build_url_options(
        span_id="test-span-id",
        trace_id="test-trace-id",
        tags={"test-key": "test-value"},
        span_kind="test-span-kind",
        span_name="test-span-name",
        ml_app="test-ml-app",
        from_timestamp="test-from-timestamp",
        to_timestamp="test-to-timestamp",
    )
    assert url_options == {
        "filter[span_id]": "test-span-id",
        "filter[trace_id]": "test-trace-id",
        "filter[tag][test-key]": "test-value",
        "filter[span_kind]": "test-span-kind",
        "filter[span_name]": "test-span-name",
        "filter[ml_app]": "test-ml-app",
        "filter[from]": "test-from-timestamp",
        "filter[to]": "test-to-timestamp",
        "page[limit]": 100,
    }


def test_export_spans_client_build_url_options_empty():
    export_spans_client = LLMObsExportSpansClient(
        api_key="test-api-key",
        app_key="test-app-key",
        site="test-site",
    )
    url_options = export_spans_client._build_url_options()

    assert url_options == {
        "page[limit]": 100,
    }


@mock.patch("ddtrace.llmobs._writer.LLMObsExportSpansClient._request")
def test_export_spans_client_export_spans(mock_request):
    """Test successful export_spans call with pagination."""
    export_spans_client = LLMObsExportSpansClient(
        api_key="test-api-key",
        app_key="test-app-key",
        site="test-site",
    )

    mock_response_page_1 = Response(
        status=200,
        body=(
            '{"data": [{"attributes": {"span_id": "span-1", "trace_id": "trace-1"}}], '
            '"meta": {"page": {"after": "cursor-1"}}}'
        ),
    )
    mock_response_page_2 = Response(
        status=200,
        body=('{"data": [{"attributes": {"span_id": "span-2", "trace_id": "trace-1"}}], "meta": {"page": {}}}'),
    )
    mock_request.side_effect = [mock_response_page_1, mock_response_page_2]

    spans = list(
        export_spans_client.export_spans(
            trace_id="trace-1",
        )
    )

    assert mock_request.call_count == 2

    assert len(spans) == 2
    assert spans[0] == {"span_id": "span-1", "trace_id": "trace-1"}
    assert spans[1] == {"span_id": "span-2", "trace_id": "trace-1"}


@mock.patch("ddtrace.llmobs._writer.LLMObsExportSpansClient._request")
def test_export_spans_client_export_spans_error(mock_request, mock_writer_logs):
    """Test export_spans logs error on non-200 response."""
    export_spans_client = LLMObsExportSpansClient(
        api_key="test-api-key",
        app_key="test-app-key",
        site="test-site",
    )

    mock_response = Response(
        status=500,
        body='{"error": "Internal server error"}',
    )
    mock_request.return_value = mock_response

    spans = list(
        export_spans_client.export_spans(
            span_id="test-span-id",
        )
    )

    assert len(spans) == 0

    mock_writer_logs.error.assert_called_with(
        "Failed to export spans: page=%d, status=%d, response=%s",
        0,
        500,
        '{"error": "Internal server error"}',
    )
