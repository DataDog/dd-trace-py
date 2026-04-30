from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR_UNSAMPLED
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._unsampled import LLMObsUnsampledStripProcessor
from ddtrace.trace import Span


def _llmobs_payload():
    return {
        LLMOBS_STRUCT.NAME: "my_span",
        LLMOBS_STRUCT.ML_APP: "test-app",
        LLMOBS_STRUCT.METRICS: {"input_tokens": 10, "output_tokens": 5},
        LLMOBS_STRUCT.TAGS: {"env": "prod"},
        LLMOBS_STRUCT.META: {
            LLMOBS_STRUCT.SPAN: {LLMOBS_STRUCT.KIND: "llm"},
            LLMOBS_STRUCT.MODEL_NAME: "gpt-4",
            LLMOBS_STRUCT.MODEL_PROVIDER: "openai",
            LLMOBS_STRUCT.METADATA: {
                "temperature": 0.5,
                LLMOBS_STRUCT.METADATA_DD: {LLMOBS_STRUCT.COST_TAGS: ["team:llm-obs"]},
            },
            LLMOBS_STRUCT.INPUT: {
                LLMOBS_STRUCT.MESSAGES: [{"role": "user", "content": "hello"}],
                LLMOBS_STRUCT.PROMPT: {"template": "Say hi"},
            },
            LLMOBS_STRUCT.OUTPUT: {
                LLMOBS_STRUCT.MESSAGES: [{"role": "assistant", "content": "hi"}],
            },
            LLMOBS_STRUCT.TOOL_DEFINITIONS: [{"name": "calc"}],
            LLMOBS_STRUCT.EXPECTED_OUTPUT: "hi",
        },
    }


def _make_span(priority=None, with_llmobs=True):
    span = Span(name="root")
    if priority is not None:
        span.context.sampling_priority = priority
    if with_llmobs:
        span._set_struct_tag(LLMOBS_STRUCT.KEY, _llmobs_payload())
    return span


def _data(span):
    return span._get_struct_tag(LLMOBS_STRUCT.KEY)


def test_strips_io_when_priority_is_auto_reject():
    span = _make_span(priority=0)
    LLMObsUnsampledStripProcessor().process_trace([span])
    data = _data(span)
    meta = data[LLMOBS_STRUCT.META]
    assert LLMOBS_STRUCT.INPUT not in meta
    assert LLMOBS_STRUCT.OUTPUT not in meta
    assert LLMOBS_STRUCT.EXPECTED_OUTPUT not in meta
    assert LLMOBS_STRUCT.TOOL_DEFINITIONS not in meta
    assert meta[LLMOBS_STRUCT.MODEL_NAME] == "gpt-4"
    assert meta[LLMOBS_STRUCT.MODEL_PROVIDER] == "openai"
    assert meta[LLMOBS_STRUCT.SPAN][LLMOBS_STRUCT.KIND] == "llm"
    assert meta[LLMOBS_STRUCT.METADATA]["temperature"] == 0.5
    assert meta[LLMOBS_STRUCT.METADATA][LLMOBS_STRUCT.METADATA_DD][LLMOBS_STRUCT.COST_TAGS] == ["team:llm-obs"]
    assert data[LLMOBS_STRUCT.METRICS] == {"input_tokens": 10, "output_tokens": 5}
    assert data[LLMOBS_STRUCT.TAGS] == {"env": "prod"}
    assert data[LLMOBS_STRUCT.NAME] == "my_span"
    assert data[LLMOBS_STRUCT.ML_APP] == "test-app"
    errs = meta[LLMOBS_STRUCT.METADATA][LLMOBS_STRUCT.METADATA_DD]["collection_errors"]
    assert DROPPED_IO_COLLECTION_ERROR_UNSAMPLED in errs


def test_strips_io_when_priority_is_user_reject():
    span = _make_span(priority=-1)
    LLMObsUnsampledStripProcessor().process_trace([span])
    meta = _data(span)[LLMOBS_STRUCT.META]
    assert LLMOBS_STRUCT.INPUT not in meta
    assert LLMOBS_STRUCT.OUTPUT not in meta


def test_does_not_strip_when_priority_is_auto_keep():
    span = _make_span(priority=1)
    LLMObsUnsampledStripProcessor().process_trace([span])
    meta = _data(span)[LLMOBS_STRUCT.META]
    assert LLMOBS_STRUCT.INPUT in meta
    assert LLMOBS_STRUCT.OUTPUT in meta
    assert LLMOBS_STRUCT.TOOL_DEFINITIONS in meta
    assert LLMOBS_STRUCT.EXPECTED_OUTPUT in meta


def test_does_not_strip_when_priority_is_user_keep():
    span = _make_span(priority=2)
    LLMObsUnsampledStripProcessor().process_trace([span])
    meta = _data(span)[LLMOBS_STRUCT.META]
    assert LLMOBS_STRUCT.INPUT in meta
    assert LLMOBS_STRUCT.OUTPUT in meta


def test_does_not_strip_when_priority_is_unset():
    span = _make_span(priority=None)
    LLMObsUnsampledStripProcessor().process_trace([span])
    meta = _data(span)[LLMOBS_STRUCT.META]
    assert LLMOBS_STRUCT.INPUT in meta
    assert LLMOBS_STRUCT.OUTPUT in meta


def test_skips_spans_without_llmobs_struct():
    span = _make_span(priority=0, with_llmobs=False)
    LLMObsUnsampledStripProcessor().process_trace([span])
    assert _data(span) is None


def test_strips_all_llmobs_spans_in_trace():
    root = _make_span(priority=0)
    child = Span(name="child")
    child._local_root_value = root
    child._set_struct_tag(LLMOBS_STRUCT.KEY, _llmobs_payload())
    LLMObsUnsampledStripProcessor().process_trace([root, child])
    for span in (root, child):
        meta = _data(span)[LLMOBS_STRUCT.META]
        assert LLMOBS_STRUCT.INPUT not in meta
        assert LLMOBS_STRUCT.OUTPUT not in meta


def test_idempotent_collection_error_marker():
    span = _make_span(priority=0)
    proc = LLMObsUnsampledStripProcessor()
    proc.process_trace([span])
    proc.process_trace([span])
    errs = _data(span)[LLMOBS_STRUCT.META][LLMOBS_STRUCT.METADATA][LLMOBS_STRUCT.METADATA_DD]["collection_errors"]
    assert errs.count(DROPPED_IO_COLLECTION_ERROR_UNSAMPLED) == 1


def test_empty_trace_is_passthrough():
    assert LLMObsUnsampledStripProcessor().process_trace([]) == []
