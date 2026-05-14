from pydantic import BaseModel
import pytest

from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _normalize_wire_trace_id_to_hex
from ddtrace.llmobs._utils import _trace_id_to_wire
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
    assert safe_json({"name": "hello world", "age": 123}) == '{"age": 123, "name": "hello world"}'


def test_json_serialize_pydantic_model():
    class Model(BaseModel):
        name: str
        age: int

    pydantic_model = Model(name="hello world", age=123)
    encoded_model = safe_json(pydantic_model)
    assert encoded_model == '{"age": 123, "name": "hello world"}'


def test_json_serialize_pydantic_model_with_complex_field():
    class Metadata(BaseModel):
        key: str
        value: str

    class Model(BaseModel):
        name: str
        metadata: Metadata

    pydantic_model = Model(name="hello world", metadata=Metadata(key="goodbye", value="cruel world"))
    encoded_model = safe_json(pydantic_model)
    assert encoded_model == '{"metadata": {"key": "goodbye", "value": "cruel world"}, "name": "hello world"}'


def test_json_serialize_pydantic_model_in_list():
    class Model(BaseModel):
        name: str
        age: int

    result = safe_json([Model(name="alice", age=30), Model(name="bob", age=25)])
    assert result == '[{"age": 30, "name": "alice"}, {"age": 25, "name": "bob"}]'


def test_json_serialize_pydantic_model_in_tuple():
    class Model(BaseModel):
        name: str
        age: int

    result = safe_json((Model(name="alice", age=30), "hello"))
    assert result == '[{"age": 30, "name": "alice"}, "hello"]'


def test_json_serialize_pydantic_model_in_dict_value():
    class Model(BaseModel):
        name: str

    result = safe_json({"result": Model(name="alice")})
    assert result == '{"result": {"name": "alice"}}'


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


class TestAnnotateLLMObsSpanData:
    def test_populates_meta_struct(self, llmobs):
        """All annotated fields are stored in the correct nested positions."""
        messages_in = [{"role": "user", "content": "hello"}]
        messages_out = [{"role": "assistant", "content": "hi"}]
        tools = [{"name": "calc", "description": "A calculator", "parameters": {}}]
        links = [{"span_id": "abc", "trace_id": "def", "attributes": {"from": "output", "to": "input"}}]
        with llmobs.llm(name="test_span") as span:
            _annotate_llmobs_span_data(
                span,
                name="my_span",
                kind="llm",
                ml_app="test-app",
                model_name="gpt-4",
                model_provider="openai",
                session_id="sess-1",
                parent_id="parent-1",
                trace_id="12345",
                input_messages=messages_in,
                output_messages=messages_out,
                metadata={"temperature": 0.5},
                metrics={"input_tokens": 10},
                tags={"env": "prod"},
                tool_definitions=tools,
                span_links=links,
            )
            data = span._get_struct_tag(LLMOBS_STRUCT.KEY)
            assert data[LLMOBS_STRUCT.NAME] == "my_span"
            assert data[LLMOBS_STRUCT.ML_APP] == "test-app"
            assert data[LLMOBS_STRUCT.SESSION_ID] == "sess-1"
            assert data[LLMOBS_STRUCT.PARENT_ID] == "parent-1"
            assert data[LLMOBS_STRUCT.TRACE_ID] == "12345"
            assert data[LLMOBS_STRUCT.METRICS] == {"input_tokens": 10}
            assert {"env": "prod"}.items() <= data[LLMOBS_STRUCT.TAGS].items()
            assert data[LLMOBS_STRUCT.SPAN_LINKS] == links
            meta = data[LLMOBS_STRUCT.META]
            assert meta[LLMOBS_STRUCT.SPAN][LLMOBS_STRUCT.KIND] == "llm"
            assert meta[LLMOBS_STRUCT.MODEL_NAME] == "gpt-4"
            assert meta[LLMOBS_STRUCT.MODEL_PROVIDER] == "openai"
            assert meta[LLMOBS_STRUCT.INPUT][LLMOBS_STRUCT.MESSAGES] == messages_in
            assert meta[LLMOBS_STRUCT.OUTPUT][LLMOBS_STRUCT.MESSAGES] == messages_out
            assert meta[LLMOBS_STRUCT.METADATA] == {"temperature": 0.5}
            assert meta[LLMOBS_STRUCT.TOOL_DEFINITIONS] == tools

    def test_merges_metadata_metrics_tags_across_calls(self, llmobs):
        """metadata, metrics, and tags accumulate rather than overwrite across multiple annotate calls."""
        with llmobs.task(name="test_span") as span:
            _annotate_llmobs_span_data(
                span, metadata={"key1": "val1"}, metrics={"input_tokens": 10}, tags={"env": "prod"}
            )
            _annotate_llmobs_span_data(
                span, metadata={"key2": "val2"}, metrics={"output_tokens": 5}, tags={"version": "1.0"}
            )
            data = span._get_struct_tag(LLMOBS_STRUCT.KEY)
            assert data[LLMOBS_STRUCT.META][LLMOBS_STRUCT.METADATA] == {"key1": "val1", "key2": "val2"}
            assert data[LLMOBS_STRUCT.METRICS] == {"input_tokens": 10, "output_tokens": 5}
            assert {"env": "prod", "version": "1.0"}.items() <= data[LLMOBS_STRUCT.TAGS].items()


class TestTraceIdNormalization:
    HEX_TRACE_ID = "ef017ddb6db557ea44fb6ce732fd0687"
    DECIMAL_TRACE_ID = str(int(HEX_TRACE_ID, 16))

    def test_normalize_canonical_hex_passthrough(self):
        assert _normalize_wire_trace_id_to_hex(self.HEX_TRACE_ID) == self.HEX_TRACE_ID

    def test_normalize_decimal_converts_to_hex(self):
        assert _normalize_wire_trace_id_to_hex(self.DECIMAL_TRACE_ID) == self.HEX_TRACE_ID

    def test_normalize_small_decimal_identity(self):
        # format_trace_id returns str(int) for values <= 2^64, so small ints stay decimal.
        assert _normalize_wire_trace_id_to_hex("12345") == "12345"

    def test_normalize_non_numeric_custom_id_passthrough(self, caplog):
        import logging

        caplog.set_level(logging.DEBUG, logger="ddtrace.llmobs._utils")
        assert _normalize_wire_trace_id_to_hex("custom-trace-id-abc") == "custom-trace-id-abc"
        assert any("not canonical hex or a decimal integer" in r.message for r in caplog.records)

    def test_normalize_uppercase_hex_passthrough(self):
        up = self.HEX_TRACE_ID.upper()
        assert _normalize_wire_trace_id_to_hex(up) == up

    def test_normalize_none_and_empty(self):
        assert _normalize_wire_trace_id_to_hex(None) is None
        assert _normalize_wire_trace_id_to_hex("") is None

    def test_normalize_32_digit_decimal_wire_value_not_misclassified_as_hex(self):
        # 32-digit decimals (older-SDK str(int128) output) must round-trip as decimal,
        # otherwise _trace_id_to_wire would re-parse as base-16 on the next hop.
        decimal_wire = "50000000000000000000000000000000"
        normalized = _normalize_wire_trace_id_to_hex(decimal_wire)
        assert normalized == format_trace_id(int(decimal_wire))
        assert _trace_id_to_wire(normalized) == decimal_wire

    def test_normalize_zero_padded_hex_passthrough_even_if_all_digits(self):
        # Leading "0" disambiguates as hex: str(int) can't emit leading zeros.
        padded_hex_all_digits = "00000000000000001234567890123456"
        assert _normalize_wire_trace_id_to_hex(padded_hex_all_digits) == padded_hex_all_digits

    def test_wire_hex_to_decimal(self):
        assert _trace_id_to_wire(self.HEX_TRACE_ID) == self.DECIMAL_TRACE_ID

    def test_wire_decimal_passthrough(self):
        assert _trace_id_to_wire(self.DECIMAL_TRACE_ID) == self.DECIMAL_TRACE_ID

    def test_wire_custom_passthrough(self):
        assert _trace_id_to_wire("custom-trace-id-abc") == "custom-trace-id-abc"

    def test_wire_none_and_empty(self):
        assert _trace_id_to_wire(None) is None
        assert _trace_id_to_wire("") is None

    def test_normalize_after_wire_recovers_hex(self):
        wire = _trace_id_to_wire(self.HEX_TRACE_ID)
        assert _normalize_wire_trace_id_to_hex(wire) == self.HEX_TRACE_ID
