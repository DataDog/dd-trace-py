import os
from textwrap import dedent
import asyncio
import concurrent.futures

from ddtrace.internal.utils.formats import format_trace_id
import pytest

from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObsSpan
from ddtrace.llmobs import _constants as const
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._utils import _get_session_id
from tests.llmobs._utils import _expected_llmobs_llm_span_event


class TestMLApp:
    @pytest.mark.parametrize("llmobs_env", [{"DD_LLMOBS_ML_APP": "<not-a-real-app-name>"}])
    def test_tag_defaults_to_env_var(self, tracer, llmobs_env, llmobs_events):
        """Test that no ml_app defaults to the environment variable DD_LLMOBS_ML_APP."""
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        assert "ml_app:<not-a-real-app-name>" in llmobs_events[0]["tags"]

    @pytest.mark.parametrize("llmobs_env", [{"DD_LLMOBS_ML_APP": "<not-a-real-app-name>"}])
    def test_tag_overrides_env_var(self, tracer, llmobs_env, llmobs_events):
        """Test that when ml_app is set on the span, it overrides the environment variable DD_LLMOBS_ML_APP."""
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.ML_APP, "test-ml-app")
        assert "ml_app:test-ml-app" in llmobs_events[0]["tags"]

    def test_propagates_ignore_non_llmobs_spans(self, tracer, llmobs_events):
        """
        Test that when ml_app is not set, we propagate from nearest LLMObs ancestor
        even if there are non-LLMObs spans in between.
        """
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.ML_APP, "test-ml-app")
            with tracer.trace("child_span"):
                with tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
                    with tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
        assert len(llmobs_events) == 3
        for llmobs_event in llmobs_events:
            assert "ml_app:test-ml-app" in llmobs_event["tags"]


def test_set_correct_parent_id(llmobs):
    """Test that the parent_id is set as the span_id of the nearest LLMObs span in the span's ancestor tree."""
    with llmobs._instance.tracer.trace("root"):
        with llmobs.workflow("llm_span") as llm_span:
            pass
    assert llm_span._get_ctx_item(PARENT_ID_KEY) is ROOT_PARENT_ID
    with llmobs.workflow("root_llm_span") as root_span:
        assert root_span._get_ctx_item(PARENT_ID_KEY) is ROOT_PARENT_ID
        with llmobs._instance.tracer.trace("child_span") as child_span:
            assert child_span._get_ctx_item(PARENT_ID_KEY) is None
            with llmobs.task("llm_span") as grandchild_span:
                assert grandchild_span._get_ctx_item(PARENT_ID_KEY) == str(root_span.span_id)


class TestSessionId:
    def test_propagate_from_ancestors(self, tracer):
        """
        Test that session_id is propagated from the nearest LLMObs span in the span's ancestor tree
        if no session_id is not set on the span itself.
        """
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
            root_span._set_ctx_item(const.SESSION_ID, "test_session_id")
            with tracer.trace("child_span"):
                with tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                    pass
        assert _get_session_id(llm_span) == "test_session_id"

    def test_if_set_manually(self, tracer):
        """Test that session_id is extracted from the span if it is already set manually."""
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
            root_span._set_ctx_item(const.SESSION_ID, "test_session_id")
            with tracer.trace("child_span"):
                with tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                    llm_span._set_ctx_item(const.SESSION_ID, "test_different_session_id")
        assert _get_session_id(llm_span) == "test_different_session_id"

    def test_propagates_ignore_non_llmobs_spans(self, tracer, llmobs_events):
        """
        Test that when session_id is not set, we propagate from nearest LLMObs ancestor
        even if there are non-LLMObs spans in between.
        """
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.SESSION_ID, "session-123")
            with tracer.trace("child_span"):
                with tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
                    with tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")

        llm_event, grandchild_event, great_grandchild_event = llmobs_events
        assert llm_event["session_id"] == "session-123"
        assert grandchild_event["session_id"] == "session-123"
        assert great_grandchild_event["session_id"] == "session-123"


class TestLLMIOProcessing:
    """Test the input and output processing features of LLMObs."""

    def _remove_input_output(span: LLMObsSpan):
        for message in span.input + span.output:
            message["content"] = ""
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_remove_input_output)])
    def test_input_output_processor_remove(self, llmobs, llmobs_enable_opts, llmobs_events):
        with llmobs.llm() as llm_span:
            llmobs.annotate(llm_span, input_data="value", output_data="value")

        assert llmobs_events[0]["meta"]["input"] == {"messages": [{"content": "", "role": ""}]}
        assert llmobs_events[0]["meta"]["output"] == {"messages": [{"content": "", "role": ""}]}

        # Also test input output values are removed
        with llmobs.llm() as llm_span:
            llm_span._set_ctx_item("_ml_obs.meta.input.value", "value")
            llm_span._set_ctx_item("_ml_obs.meta.output.value", "value")

        assert llmobs_events[1]["meta"]["input"] == {"value": ""}
        assert llmobs_events[1]["meta"]["output"] == {"value": ""}

    def _mutate_input_output_messages(span: LLMObsSpan):
        for message in span.input + span.output:
            message["content"] += " processed"
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_mutate_input_output_messages)])
    def test_input_output_processor_mutate(self, llmobs, llmobs_enable_opts, llmobs_events):
        with llmobs.llm() as llm_span:
            llmobs.annotate(llm_span, input_data="value", output_data=[{"content": "value"}, {"content": "value2"}])
        assert llmobs_events[0]["meta"]["input"] == {"messages": [{"content": "value processed", "role": ""}]}
        assert llmobs_events[0]["meta"]["output"] == {
            "messages": [{"content": "value processed", "role": ""}, {"content": "value2 processed", "role": ""}]
        }

    def _conditional_input_output_processor(span: LLMObsSpan):
        if span.get_tag("scrub_values") == "1":
            for message in span.input + span.output:
                message["content"] = "redacted"
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_conditional_input_output_processor)])
    def test_annotated_span(self, llmobs, llmobs_enable_opts, llmobs_events):
        with llmobs.annotation_context(tags={"scrub_values": "1"}):
            with llmobs.llm() as llm_span:
                llmobs.annotate(llm_span, input_data="value", output_data="value")
        assert llmobs_events[0]["meta"]["input"] == {"messages": [{"content": "redacted", "role": ""}]}
        assert llmobs_events[0]["meta"]["output"] == {"messages": [{"content": "redacted", "role": ""}]}

    def _bad_return_type_processor(span: LLMObsSpan) -> str:
        return "not a span"

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_bad_return_type_processor)])
    def test_processor_bad_return_type(self, llmobs, llmobs_enable_opts, llmobs_events):
        """Test that a processor that returns a non-LLMObsSpan type is ignored."""
        with llmobs.llm() as llm_span:
            llmobs.annotate(llm_span, input_data="value", output_data="value")
        assert llmobs_events[0]["meta"]["input"] == {"messages": [{"content": "value", "role": ""}]}
        assert llmobs_events[0]["meta"]["output"] == {"messages": [{"content": "value", "role": ""}]}

    def test_ddtrace_run_register_processor(self, ddtrace_run_python_code_in_subprocess, llmobs_backend):
        """Users using ddtrace-run can register a processor to be called on each LLMObs span."""
        env = os.environ.copy()
        env["DD_LLMOBS_ML_APP"] = "test-ml-app"
        env["DD_API_KEY"] = "test-api-key"
        env["DD_LLMOBS_ENABLED"] = "1"
        env["DD_LLMOBS_AGENTLESS_ENABLED"] = "0"
        env["DD_TRACE_ENABLED"] = "0"
        env["DD_TRACE_AGENT_URL"] = llmobs_backend.url()
        out, err, status, _ = ddtrace_run_python_code_in_subprocess(
            dedent(
                """
            from ddtrace.llmobs import LLMObs

            def span_processor(s):
                if s.get_tag("scrub_values") == "1":
                    s.input = [{"content": "scrubbed"}]
                    s.output = [{"content": "scrubbed"}]
                return s

            LLMObs.register_processor(span_processor)

            with LLMObs.llm("openai.request") as llm_span:
                LLMObs.annotate(llm_span, input_data="value", output_data="value", tags={"scrub_values": "1"})
            with LLMObs.llm("openai.request") as llm_span:
                LLMObs.annotate(llm_span, input_data="value", output_data="value", tags={"scrub_values": "0"})
            """
            ),
            env=env,
        )
        assert out == b""
        assert status == 0, err
        assert err.decode() == ""
        events = llmobs_backend.wait_for_num_events(num=1)
        traces = events[0]
        assert len(traces) == 2
        assert "scrub_values:1" in traces[0]["spans"][0]["tags"]
        assert traces[0]["spans"][0]["meta"]["input"]["messages"][0]["content"] == "scrubbed"
        assert traces[0]["spans"][0]["meta"]["output"]["messages"][0]["content"] == "scrubbed"
        assert "scrub_values:0" in traces[1]["spans"][0]["tags"]
        assert traces[1]["spans"][0]["meta"]["input"]["messages"][0]["content"] == "value"
        assert traces[1]["spans"][0]["meta"]["output"]["messages"][0]["content"] == "value"

    def test_register_unregister_processor(self, llmobs, llmobs_events):
        def _sp(s):
            s.input = [{"content": "scrubbed"}]
            return s

        # register
        llmobs.register_processor(_sp)
        with llmobs.llm("openai.request") as llm_span:
            llmobs.annotate(llm_span, input_data="value")
        assert llmobs_events[0]["meta"]["input"]["messages"][0]["content"] == "scrubbed"

        # unregister
        llmobs.register_processor(None)
        with llmobs.llm("openai.request") as llm_span:
            llmobs.annotate(llm_span, input_data="value")
        assert llmobs_events[1]["meta"]["input"]["messages"][0]["content"] == "value"

    def test_processor_error_is_logged(self, ddtrace_run_python_code_in_subprocess, llmobs_backend):
        """Ensure that when an exception is raised an exception is logged."""
        env = os.environ.copy()
        env["DD_LLMOBS_ML_APP"] = "test-ml-app"
        env["DD_API_KEY"] = "test-api-key"
        env["DD_LLMOBS_AGENTLESS_ENABLED"] = "0"
        env["DD_TRACE_ENABLED"] = "0"
        env["DD_TRACE_AGENT_URL"] = llmobs_backend.url()
        env["DD_TRACE_LOGGING_RATE"] = "0"
        out, err, status, _ = ddtrace_run_python_code_in_subprocess(
            dedent(
                """
            from ddtrace.llmobs import LLMObs

            def raising_span_processor(s):
                raise Exception("something bad happened")

            LLMObs.enable(span_processor=raising_span_processor)
            with LLMObs.llm("openai.request") as llm_span:
                LLMObs.annotate(llm_span, input_data="value", output_data="value", tags={"scrub_values": "1"})

            def bad_return_value_processor(s):
                return "not a span"

            LLMObs.register_processor(bad_return_value_processor)
            with LLMObs.llm("openai.request") as llm_span:
                LLMObs.annotate(llm_span, input_data="value", output_data="value", tags={"scrub_values": "1"})
            """
            ),
            env=env,
        )
        assert status == 0, err
        assert b"something bad happened" in err
        assert b"User span processor must return an LLMObsSpan" in err
        assert out == b""


def test_input_value_is_set(tracer, llmobs_events):
    """Test that input value is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_VALUE, "value")
    assert llmobs_events[0]["meta"]["input"]["value"] == "value"


def test_input_messages_are_set(tracer, llmobs_events):
    """Test that input messages are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_MESSAGES, [{"content": "message", "role": "user"}])
    assert llmobs_events[0]["meta"]["input"]["messages"] == [{"content": "message", "role": "user"}]


def test_output_messages_are_set(tracer, llmobs_events):
    """Test that output messages are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.OUTPUT_MESSAGES, [{"content": "message", "role": "user"}])
    assert llmobs_events[0]["meta"]["output"]["messages"] == [{"content": "message", "role": "user"}]


def test_output_value_is_set(tracer, llmobs_events):
    """Test that output value is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.OUTPUT_VALUE, "value")
    assert llmobs_events[0]["meta"]["output"]["value"] == "value"


def test_prompt_is_set(tracer, llmobs_events):
    """Test that prompt is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_PROMPT, {"variables": {"var1": "var2"}})
    assert llmobs_events[0]["meta"]["input"]["prompt"] == {"variables": {"var1": "var2"}}


def test_prompt_is_not_set_for_non_llm_spans(tracer, llmobs_events):
    """Test that prompt is NOT set on the span event if the span is not an LLM span."""
    with tracer.trace("task_span", span_type=SpanTypes.LLM) as task_span:
        task_span._set_ctx_item(const.SPAN_KIND, "task")
        task_span._set_ctx_item(const.INPUT_VALUE, "ival")
        task_span._set_ctx_item(const.INPUT_PROMPT, {"variables": {"var1": "var2"}})
    assert llmobs_events[0]["meta"]["input"].get("prompt") is None


def test_metadata_is_set(tracer, llmobs_events):
    """Test that metadata is set on the span event if it is present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.METADATA, {"key": "value"})
    assert llmobs_events[0]["meta"]["metadata"] == {"key": "value"}


def test_metrics_are_set(tracer, llmobs_events):
    """Test that metadata is set on the span event if it is present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.METRICS, {"tokens": 100})
    assert llmobs_events[0]["metrics"] == {"tokens": 100}


def test_langchain_span_name_is_set_to_class_name(tracer, llmobs_events):
    """Test span names for langchain auto-instrumented spans is set correctly."""
    with tracer.trace(const.LANGCHAIN_APM_SPAN_NAME, resource="expected_name", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
    assert llmobs_events[0]["name"] == "expected_name"


def test_error_is_set(tracer, llmobs_events):
    """Test that error is set on the span event if it is present on the span."""
    with pytest.raises(ValueError):
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            raise ValueError("error")
    span_event = llmobs_events[0]
    assert span_event["meta"]["error.message"] == "error"
    assert "ValueError" in span_event["meta"]["error.type"]
    assert 'raise ValueError("error")' in span_event["meta"]["error.stack"]


def test_model_provider_defaults_to_custom(tracer, llmobs_events):
    """Test that model provider defaults to "custom" if not provided."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.MODEL_NAME, "model_name")
    span_event = llmobs_events[0]
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "custom"


def test_model_not_set_if_not_llm_kind_span(tracer, llmobs_events):
    """Test that model name and provider not set if non-LLM span."""
    with tracer.trace("root_workflow_span", span_type=SpanTypes.LLM) as span:
        span._set_ctx_item(const.SPAN_KIND, "workflow")
        span._set_ctx_item(const.MODEL_NAME, "model_name")
    span_event = llmobs_events[0]
    assert "model_name" not in span_event["meta"]
    assert "model_provider" not in span_event["meta"]


def test_model_and_provider_are_set(tracer, llmobs_events):
    """Test that model and provider are set on the span event if they are present on the LLM-kind span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.MODEL_NAME, "model_name")
        llm_span._set_ctx_item(const.MODEL_PROVIDER, "model_provider")
    span_event = llmobs_events[0]
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "model_provider"


def test_malformed_span_logs_error_instead_of_raising(tracer, llmobs_events, mock_llmobs_logs):
    """Test that a trying to create a span event from a malformed span will log an error instead of crashing."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        pass  # span does not have SPAN_KIND tag
    mock_llmobs_logs.error.assert_called_with(
        "Error generating LLMObs span event for span %s, likely due to malformed span", llm_span, exc_info=True
    )
    assert len(llmobs_events) == 0


def test_only_generate_span_events_from_llmobs_spans(tracer, llmobs_events):
    """Test that we only generate LLMObs span events for LLM span types."""
    with tracer.trace("root_llm_span", service="tests.llmobs", span_type=SpanTypes.LLM) as root_span:
        root_span._set_ctx_item(const.SPAN_KIND, "llm")
        with tracer.trace("child_span"):
            pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(root_span, "llm")


def test_utf_non_ascii_io(llmobs, llmobs_backend):
    with llmobs.workflow() as workflow_span:
        with llmobs.llm(model_name="gpt-3.5-turbo-0125") as llm_span:
            llmobs.annotate(llm_span, input_data="안녕, 지금 몇 시야?")
            llmobs.annotate(workflow_span, input_data="안녕, 지금 몇 시야?")
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["input"]["messages"][0]["content"] == "안녕, 지금 몇 시야?"
    assert events[0][1]["spans"][0]["meta"]["input"]["value"] == "안녕, 지금 몇 시야?"


def test_non_utf8_inputs_outputs(llmobs, llmobs_backend):
    """Test that latin1 encoded inputs and outputs are correctly decoded."""
    with llmobs.llm(model_name="gpt-3.5-turbo-0125") as span:
        llmobs.annotate(
            span,
            input_data="The first Super Bowl (aka First AFL–NFL World Championship Game), was played in 1967.",
        )

    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert (
        events[0][0]["spans"][0]["meta"]["input"]["messages"][0]["content"]
        == "The first Super Bowl (aka First AFL–NFL World Championship Game), was played in 1967."
    )


def test_structured_io_data(llmobs, llmobs_backend):
    """Ensure that structured output data is correctly serialized."""
    for m in [llmobs.workflow, llmobs.task, llmobs.llm]:
        with m() as span:
            llmobs.annotate(span, input_data={"data": "test1"}, output_data={"data": "test2"})
        events = llmobs_backend.wait_for_num_events(num=1)
        assert len(events) == 1
        assert events[0][0]["spans"][0]["meta"]["input"]["value"] == '{"data": "test1"}'
        assert events[0][0]["spans"][0]["meta"]["output"]["value"] == '{"data": "test2"}'


def test_structured_prompt_data(llmobs, llmobs_backend):
    with llmobs.llm() as span:
        llmobs.annotate(span, prompt={"template": "test {{value}}"})
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["input"] == {
        "prompt": {
            "template": "test {{value}}",
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        },
    }


def test_structured_io_data_unserializable(llmobs, llmobs_backend):
    class CustomObj:
        pass

    expected_repr = '"<tests.llmobs.test_llmobs.test_structured_io_data_unserializable.<locals>.CustomObj object at 0x'
    for m in [llmobs.workflow, llmobs.task, llmobs.llm, llmobs.retrieval]:
        with m() as span:
            llmobs.annotate(span, input_data=CustomObj(), output_data=CustomObj())
        events = llmobs_backend.wait_for_num_events(num=1)
        assert len(events) == 1
        assert expected_repr in events[0][0]["spans"][0]["meta"]["input"]["value"]
        assert expected_repr in events[0][0]["spans"][0]["meta"]["output"]["value"]

@pytest.mark.asyncio
async def test_asyncio_trace_id_propagation(llmobs, llmobs_events):
    """Test that LLMObs trace ID and APM trace ID are properly propagated in async contexts."""
    
    async def async_task():
        with llmobs.workflow("inner_workflow"):
            return 42

    async def another_async_task():
        with llmobs.workflow("another_workflow"):
            return 43

    with llmobs.workflow("outer_workflow"):
        results = await asyncio.gather(async_task(), another_async_task())
        assert results == [42, 43]

    assert len(llmobs_events) == 3
    outer_workflow, inner_workflow, another_workflow = llmobs_events

    # All spans should share the same trace ID
    assert outer_workflow["trace_id"] == inner_workflow["trace_id"] == another_workflow["trace_id"]
    assert outer_workflow["_dd"]["apm_trace_id"] == inner_workflow["_dd"]["apm_trace_id"] == another_workflow["_dd"]["apm_trace_id"]
    assert outer_workflow["trace_id"] != outer_workflow["_dd"]["apm_trace_id"]

def test_trace_id_propagation_with_non_llm_parent(llmobs, llmobs_events):
    """Test that LLMObs trace ID and APM trace ID propagate correctly with non-LLM parent spans."""
    with llmobs._instance.tracer.trace("parent_non_llm") as parent:
        with llmobs.workflow("first_child"):
            pass
        with llmobs.workflow("second_child") as second_child:
            with llmobs.workflow("grandchild"):
                pass

    assert len(llmobs_events) == 3
    first_child_event, second_child_event, grandchild_event = llmobs_events

    # First child should have different trace ID from second child + grandchild
    assert first_child_event["trace_id"] != second_child_event["trace_id"]
    assert second_child_event["trace_id"] == grandchild_event["trace_id"]
    
    # APM trace ID should match parent span for all
    parent_trace_id = format_trace_id(parent.trace_id)
    assert first_child_event["_dd"]["apm_trace_id"] == parent_trace_id
    assert second_child_event["_dd"]["apm_trace_id"] == parent_trace_id
    assert grandchild_event["_dd"]["apm_trace_id"] == parent_trace_id

    # LLMObs trace IDs should be different from APM trace ID
    assert first_child_event["trace_id"] != first_child_event["_dd"]["apm_trace_id"]
    assert second_child_event["trace_id"] != second_child_event["_dd"]["apm_trace_id"]


def test_trace_id_propagation_threaded(llmobs, llmobs_events):
    """Test that LLMObs trace ID and APM trace ID propagate correctly when child span is created in a separate thread."""
    def child_fn():
        with llmobs.llm("child_llm"):
            return 42

    with llmobs.llm("parent_llm") as parent:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(child_fn)
            result = future.result()
            assert result == 42

    assert len(llmobs_events) == 2
    parent_event, child_event = llmobs_events

    # Both spans should share the same trace ID
    assert parent_event["trace_id"] == child_event["trace_id"]
    
    # APM trace IDs should match 
    assert parent_event["_dd"]["apm_trace_id"] == child_event["_dd"]["apm_trace_id"]

    # LLMObs trace IDs should be different from APM trace ID
    assert parent_event["trace_id"] != parent_event["_dd"]["apm_trace_id"]
    assert child_event["trace_id"] != child_event["_dd"]["apm_trace_id"]



