import asyncio
import os
from textwrap import dedent
from typing import Optional

import pytest

from ddtrace.ext import SpanTypes
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs import LLMObsSpan
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY
from ddtrace.llmobs._constants import LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_cost_tags
from ddtrace.llmobs._utils import get_llmobs_input
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_input_prompt
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_model_name
from ddtrace.llmobs._utils import get_llmobs_model_provider
from ddtrace.llmobs._utils import get_llmobs_output
from ddtrace.llmobs._utils import get_llmobs_output_messages
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.llmobs.types import Prompt


class TestMLApp:
    @pytest.mark.parametrize("llmobs_env", [{"DD_LLMOBS_ML_APP": "<not-a-real-app-name>"}])
    def test_tag_defaults_to_env_var(self, llmobs, tracer, llmobs_env):
        """Test that no ml_app defaults to the environment variable DD_LLMOBS_ML_APP."""
        with llmobs.workflow("root_llm_span") as span:
            pass
        assert get_llmobs_tags(span)["ml_app"] == "<not-a-real-app-name>"

    @pytest.mark.parametrize("llmobs_env", [{"DD_LLMOBS_ML_APP": "<not-a-real-app-name>"}])
    def test_tag_overrides_env_var(self, llmobs, tracer, llmobs_env):
        """Test that when ml_app is set on the span, it overrides the environment variable DD_LLMOBS_ML_APP."""
        with llmobs.workflow("root_llm_span", ml_app="test-ml-app") as span:
            pass
        assert get_llmobs_tags(span)["ml_app"] == "test-ml-app"

    def test_propagates_ignore_non_llmobs_spans(self, llmobs, tracer, test_spans):
        """
        Test that when ml_app is not set, we propagate from nearest LLMObs ancestor
        even if there are non-LLMObs spans in between.
        """
        with llmobs.workflow("root_llm_span", ml_app="test-ml-app"):
            with tracer.trace("child_span"):
                with llmobs.workflow("llm_grandchild_span"):
                    with llmobs.workflow("great_grandchild_span"):
                        pass
        spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
        assert len(spans) == 3
        for span in spans:
            assert get_llmobs_tags(span)["ml_app"] == "test-ml-app"


def test_set_correct_parent_id(llmobs, tracer):
    """Test that the parent_id is set as the span_id of the nearest LLMObs span in the span's ancestor tree."""
    with tracer.trace("root"):
        with llmobs.workflow("llm_span") as llm_span:
            assert get_llmobs_parent_id(llm_span) == ROOT_PARENT_ID
    with llmobs.workflow("root_llm_span") as root_span:
        assert get_llmobs_parent_id(root_span) == ROOT_PARENT_ID
        with tracer.trace("child_span") as child_span:
            assert get_llmobs_parent_id(child_span) is None
            with llmobs.task("llm_span") as grandchild_span:
                assert get_llmobs_parent_id(grandchild_span) == str(root_span.span_id)


class TestSessionId:
    def test_propagate_from_ancestors(self, llmobs, tracer, test_spans):
        """
        Test that session_id is propagated from the nearest LLMObs span in the span's ancestor tree
        if no session_id is not set on the span itself.
        """
        with llmobs.workflow("root_llm_span", session_id="test_session_id"):
            with tracer.trace("child_span"):
                with llmobs.task("llm_span"):
                    pass
        spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
        for span in spans:
            assert get_llmobs_session_id(span) == "test_session_id"

    def test_if_set_manually(self, llmobs, tracer):
        """Test that session_id is extracted from the span if it is already set manually."""
        with llmobs.workflow("root_llm_span", session_id="test_session_id") as workflow_span:
            with tracer.trace("child_span"):
                with llmobs.task("llm_span", session_id="test_different_session_id") as task_span:
                    pass
        assert get_llmobs_span_name(task_span) == "llm_span"
        assert get_llmobs_session_id(task_span) == "test_different_session_id"
        assert get_llmobs_span_name(workflow_span) == "root_llm_span"
        assert get_llmobs_session_id(workflow_span) == "test_session_id"

    def test_propagates_ignore_non_llmobs_spans(self, llmobs, tracer, test_spans):
        """
        Test that when session_id is not set, we propagate from nearest LLMObs ancestor
        even if there are non-LLMObs spans in between.
        """
        with llmobs.workflow("root_llm_span", session_id="session-123"):
            with tracer.trace("child_span"):
                with llmobs.workflow("llm_grandchild_span"):
                    with llmobs.workflow("great_grandchild_span"):
                        pass

        spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
        for span in spans:
            assert get_llmobs_session_id(span) == "session-123"


class TestLLMIOProcessing:
    """Test the input and output processing features of LLMObs."""

    def _remove_input_output(span: LLMObsSpan):
        for message in span.input + span.output:
            message["content"] = ""
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_remove_input_output)])
    def test_input_output_processor_remove(self, llmobs, llmobs_enable_opts):
        with llmobs.llm() as messages_span:
            llmobs.annotate(messages_span, input_data="value", output_data="value")

        assert get_llmobs_input(messages_span) == {"messages": [{"content": "", "role": ""}]}
        assert get_llmobs_output(messages_span) == {"messages": [{"content": "", "role": ""}]}

        # Also test input output values are removed
        with llmobs.llm() as values_span:
            _annotate_llmobs_span_data(values_span, input_value="value")
            _annotate_llmobs_span_data(values_span, output_value="value")

        assert get_llmobs_input(values_span) == {"value": ""}
        assert get_llmobs_output(values_span) == {"value": ""}

    def _mutate_input_output_messages(span: LLMObsSpan):
        for message in span.input + span.output:
            message["content"] += " processed"
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_mutate_input_output_messages)])
    def test_input_output_processor_mutate(self, llmobs, llmobs_enable_opts):
        with llmobs.llm() as llm_span:
            llmobs.annotate(llm_span, input_data="value", output_data=[{"content": "value"}, {"content": "value2"}])
        assert get_llmobs_input(llm_span) == {"messages": [{"content": "value processed", "role": ""}]}
        assert get_llmobs_output(llm_span) == {
            "messages": [{"content": "value processed", "role": ""}, {"content": "value2 processed", "role": ""}]
        }

    def _conditional_input_output_processor(span: LLMObsSpan):
        if span.get_tag("scrub_values") == "1":
            for message in span.input + span.output:
                message["content"] = "redacted"
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_conditional_input_output_processor)])
    def test_annotated_span(self, llmobs, llmobs_enable_opts):
        with llmobs.annotation_context(tags={"scrub_values": "1"}):
            with llmobs.llm() as llm_span:
                llmobs.annotate(llm_span, input_data="value", output_data="value")
        assert get_llmobs_input(llm_span) == {"messages": [{"content": "redacted", "role": ""}]}
        assert get_llmobs_output(llm_span) == {"messages": [{"content": "redacted", "role": ""}]}

    def _bad_return_type_processor(span: LLMObsSpan) -> str:
        return "not a span"

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_bad_return_type_processor)])
    def test_processor_bad_return_type(self, llmobs, llmobs_enable_opts):
        """Test that a processor that returns a non-LLMObsSpan type is ignored."""
        with llmobs.llm() as llm_span:
            llmobs.annotate(llm_span, input_data="value", output_data="value")
        assert get_llmobs_input(llm_span) == {"messages": [{"content": "value", "role": ""}]}
        assert get_llmobs_output(llm_span) == {"messages": [{"content": "value", "role": ""}]}

    def _omit_span_processor(span: LLMObsSpan) -> Optional[LLMObsSpan]:
        if span.get_tag("omit_span") == "true":
            return None
        return span

    @pytest.mark.parametrize("llmobs_enable_opts", [dict(span_processor=_omit_span_processor)])
    def test_processor_omit_span(self, llmobs, llmobs_enable_opts):
        """Test that a processor that returns None clears the LLMObs payload off the span."""
        with llmobs.llm() as omit_span:
            llmobs.annotate(omit_span, input_data="omit me", output_data="response", tags={"omit_span": "true"})

        with llmobs.llm() as keep_span:
            llmobs.annotate(keep_span, input_data="keep me", output_data="response", tags={"omit_span": "false"})

        # Dropped: meta_struct["_llmobs"] is cleared so the APM-shipped span
        # carries no LLMObs data to the backend.
        assert _get_llmobs_data_metastruct(omit_span) == {}
        # Kept: payload is intact for export.
        assert get_llmobs_input_messages(keep_span) == [{"content": "keep me", "role": ""}]

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

    def test_register_unregister_processor(self, llmobs):
        def _sp(s):
            s.input = [{"content": "scrubbed"}]
            return s

        # register
        llmobs.register_processor(_sp)
        with llmobs.llm("openai.request") as scrubbed_span:
            llmobs.annotate(scrubbed_span, input_data="value")
        assert get_llmobs_input_messages(scrubbed_span)[0]["content"] == "scrubbed"

        # unregister
        llmobs.register_processor(None)
        with llmobs.llm("openai.request") as unscrubbed_span:
            llmobs.annotate(unscrubbed_span, input_data="value")
        assert get_llmobs_input_messages(unscrubbed_span)[0]["content"] == "value"

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


def test_input_value_is_set(llmobs, tracer):
    """Test that input value is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", input_value="value")
    assert get_llmobs_input_value(llm_span) == "value"


def test_input_messages_are_set(llmobs, tracer):
    """Test that input messages are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", input_messages=[{"content": "message", "role": "user"}])
    assert get_llmobs_input_messages(llm_span) == [{"content": "message", "role": "user"}]


def test_output_messages_are_set(llmobs, tracer):
    """Test that output messages are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", output_messages=[{"content": "message", "role": "user"}])
    assert get_llmobs_output_messages(llm_span) == [{"content": "message", "role": "user"}]


def test_output_value_is_set(llmobs, tracer):
    """Test that output value is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", output_value="value")
    assert get_llmobs_output_value(llm_span) == "value"


def test_prompt_is_set(llmobs, tracer):
    """Test that prompt is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", prompt={"variables": {"var1": "var2"}})
    assert get_llmobs_input_prompt(llm_span) == {"variables": {"var1": "var2"}}


def test_prompt_is_not_set_for_non_llm_spans(llmobs, tracer):
    """Test that prompt is NOT set on the span event if the span is not an LLM span."""
    with tracer.trace("task_span", span_type=SpanTypes.LLM) as task_span:
        _annotate_llmobs_span_data(task_span, kind="task", input_value="ival", prompt={"variables": {"var1": "var2"}})
    assert get_llmobs_input_prompt(task_span) is None


def test_metadata_is_set(llmobs, tracer):
    """Test that metadata is set on the span event if it is present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", metadata={"key": "value"})
    assert get_llmobs_metadata(llm_span) == {"key": "value"}


def test_metrics_are_set(llmobs, tracer):
    """Test that metadata is set on the span event if it is present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", metrics={"tokens": 100})
    assert get_llmobs_metrics(llm_span) == {"tokens": 100}


def test_cost_tags_are_set_on_span_event(llmobs, tracer):
    """Test that cost tags are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(
            llm_span,
            kind="llm",
            tags={"team": "ml", "feature": "chatbot"},
            cost_tags=["team", "feature"],
        )
    assert get_llmobs_cost_tags(llm_span) == ["team", "feature"]


def test_langchain_span_name_is_set_to_class_name(llmobs, tracer):
    """Test span names for langchain auto-instrumented spans is set correctly."""
    with tracer.trace(LANGCHAIN_APM_SPAN_NAME, resource="expected_name", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm")
    assert get_llmobs_span_name(llm_span) == "expected_name"


def test_error_is_set(llmobs, tracer):
    """Test that error is set on the span event if it is present on the span."""
    with pytest.raises(ValueError):
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            _annotate_llmobs_span_data(llm_span, kind="llm")
            raise ValueError("error")
    error = _get_llmobs_data_metastruct(llm_span)["meta"]["error"]
    assert error["message"] == "error"
    assert "ValueError" in error["type"]
    assert 'raise ValueError("error")' in error["stack"]


def test_model_provider_defaults_to_unknown(llmobs, tracer):
    """Test that model provider defaults to "unknown" if not provided."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", model_name="model_name")
    assert get_llmobs_model_name(llm_span) == "model_name"
    assert get_llmobs_model_provider(llm_span) == UNKNOWN_MODEL_PROVIDER


def test_model_not_set_if_not_llm_kind_span(llmobs, tracer):
    """Test that model name and provider not set if non-LLM span."""
    with tracer.trace("root_workflow_span", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="workflow", model_name="model_name")
    assert get_llmobs_model_name(span) is None
    assert get_llmobs_model_provider(span) is None


def test_model_and_provider_are_set(llmobs, tracer):
    """Test that model and provider are set on the span event if they are present on the LLM-kind span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        _annotate_llmobs_span_data(llm_span, kind="llm", model_name="model_name", model_provider="model_provider")
    assert get_llmobs_model_name(llm_span) == "model_name"
    assert get_llmobs_model_provider(llm_span) == "model_provider"


def test_malformed_span_logs_error_instead_of_raising(llmobs, tracer, mock_llmobs_logs):
    """A malformed span (missing span kind) is dropped and its meta_struct cleared."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        pass  # span does not have SPAN_KIND tag
    mock_llmobs_logs.error.assert_called_with(
        "Error preparing LLMObs span event for span %s, missing span kind in span context.", llm_span
    )
    assert _get_llmobs_data_metastruct(llm_span) == {}


def test_only_generate_span_events_from_llmobs_spans(llmobs, tracer, test_spans):
    """Test that we only generate LLMObs span events for LLM span types."""
    with tracer.trace("root_llm_span", service="tests.llmobs", span_type=SpanTypes.LLM) as root_span:
        _annotate_llmobs_span_data(root_span, kind="llm")
        with tracer.trace("child_span"):
            pass
    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    assert len(spans) == 1
    assert get_llmobs_span_kind(spans[0]) == "llm"


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
        llmobs.annotate(span, input_data={"data": "test1"}, prompt={"template": "test {{value}}"})
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["input"]["prompt"] == {
        "id": "unnamed-ml-app_unnamed-prompt",
        "ml_app": "unnamed-ml-app",
        "template": "test {{value}}",
        "_dd_context_variable_keys": ["context"],
        "_dd_query_variable_keys": ["question"],
    }


def test_structured_prompt_data_v2(llmobs, llmobs_backend):
    prompt = Prompt(
        id="test",
        chat_template=[{"role": "user", "content": "test {{value}}"}],
        variables={"value": "test", "context": "test", "question": "test"},
        tags={"env": "prod", "llm": "openai"},
        rag_context_variables=["context"],
        rag_query_variables=["question"],
    )
    with llmobs.llm() as span:
        llmobs.annotate(
            span,
            prompt=prompt,
        )
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["input"] == {
        "prompt": {
            "id": "test",
            "ml_app": "unnamed-ml-app",
            "chat_template": [{"role": "user", "content": "test {{value}}"}],
            "variables": {"value": "test", "context": "test", "question": "test"},
            "tags": {"env": "prod", "llm": "openai"},
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        }
    }


def test_annotate_with_tool_definitions(llmobs, llmobs_backend):
    """Test that tool_definitions parameter is correctly set on spans."""
    tool_definitions = [
        {
            "name": "get_weather",
            "description": "Get the weather for a specific location",
            "schema": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
            },
            "version": "1.0.0",
        }
    ]

    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=tool_definitions)

    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["tool_definitions"] == tool_definitions


def test_annotate_with_tool_definitions_minimal(llmobs, llmobs_backend):
    """Test that tool_definitions with only required fields work correctly."""
    tool_definitions = [{"name": "simple_tool"}]

    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=tool_definitions)

    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["tool_definitions"] == tool_definitions


def test_annotate_with_invalid_tool_definitions(llmobs, llmobs_backend):
    """Test that invalid tool_definitions are handled gracefully."""
    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions="not a list")

    # Should not crash, but tool_definitions should not be set
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert "tool_definitions" not in events[0][0]["spans"][0]["meta"]


def test_annotate_with_tool_definitions_missing_name(llmobs, llmobs_backend):
    """Test that tool_definitions without name field are rejected."""
    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=[{"description": "A tool without name"}])

    # Should not crash, but tool_definitions should not be set
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert "tool_definitions" not in events[0][0]["spans"][0]["meta"]


def test_annotate_with_tool_definitions_empty_name(llmobs, llmobs_backend):
    """Test that tool_definitions with empty name field are rejected."""
    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=[{"name": "", "description": "A tool with empty name"}])

    # Should not crash, but tool_definitions should not be set
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert "tool_definitions" not in events[0][0]["spans"][0]["meta"]


def test_annotate_with_tool_definitions_invalid_name_type(llmobs, llmobs_backend):
    """Test that tool_definitions with non-string name field are rejected."""
    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=[{"name": 123, "description": "A tool with invalid name type"}])

    # Should not crash, but tool_definitions should not be set
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert "tool_definitions" not in events[0][0]["spans"][0]["meta"]


def test_annotate_with_tool_definitions_non_dict(llmobs, llmobs_backend):
    """Test that tool_definitions with non-dictionary items are rejected."""
    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=["not a dict"])

    # Should not crash, but tool_definitions should not be set
    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert "tool_definitions" not in events[0][0]["spans"][0]["meta"]


def test_annotate_with_tool_definitions_invalid_version_type(llmobs, llmobs_backend):
    """Test that tool_definitions with non-string version field have version skipped but are otherwise kept."""
    with llmobs.llm() as span:
        llmobs.annotate(span, tool_definitions=[{"name": "my_tool", "version": 1}])

    events = llmobs_backend.wait_for_num_events(num=1)
    assert len(events) == 1
    assert events[0][0]["spans"][0]["meta"]["tool_definitions"] == [{"name": "my_tool"}]


@pytest.mark.asyncio
async def test_asyncio_trace_id_propagation(llmobs):
    """Test that LLMObs trace ID and APM trace ID are properly propagated in async contexts."""
    inner_span = None
    another_span = None

    async def async_task():
        nonlocal inner_span
        with llmobs.workflow("inner_workflow") as inner_span:
            return 42

    async def another_async_task():
        nonlocal another_span
        with llmobs.workflow("another_workflow") as another_span:
            return 43

    with llmobs.workflow("outer_workflow") as outer_span:
        results = await asyncio.gather(async_task(), another_async_task())
        assert results == [42, 43]

    # All spans should share the same LLMObs trace ID
    assert get_llmobs_trace_id(outer_span) == get_llmobs_trace_id(inner_span) == get_llmobs_trace_id(another_span)
    # APM trace IDs (read off the underlying spans) should also all match
    outer_apm_trace_id = format_trace_id(outer_span.trace_id)
    inner_apm_trace_id = format_trace_id(inner_span.trace_id)
    another_apm_trace_id = format_trace_id(another_span.trace_id)
    assert outer_apm_trace_id == inner_apm_trace_id == another_apm_trace_id
    # LLMObs trace ID should differ from APM trace ID
    assert get_llmobs_trace_id(outer_span) != outer_apm_trace_id


def test_trace_id_propagation_with_non_llm_parent(llmobs):
    """Test that LLMObs trace ID and APM trace ID propagate correctly with non-LLM parent spans."""
    with llmobs._instance.tracer.trace("parent_non_llm") as parent:
        with llmobs.workflow("first_child") as first_child:
            pass
        with llmobs.workflow("second_child") as second_child:
            with llmobs.workflow("grandchild") as grandchild:
                pass

    # First child should have different LLMObs trace ID from second child + grandchild
    assert get_llmobs_trace_id(first_child) != get_llmobs_trace_id(second_child)
    assert get_llmobs_trace_id(second_child) == get_llmobs_trace_id(grandchild)

    # APM trace ID (read off the underlying spans) should match parent span for all
    parent_trace_id = format_trace_id(parent.trace_id)
    assert format_trace_id(first_child.trace_id) == parent_trace_id
    assert format_trace_id(second_child.trace_id) == parent_trace_id
    assert format_trace_id(grandchild.trace_id) == parent_trace_id

    # LLMObs trace IDs should be different from APM trace ID
    assert get_llmobs_trace_id(first_child) != parent_trace_id
    assert get_llmobs_trace_id(second_child) != parent_trace_id


def test_llmobs_trace_id_written_to_local_root_meta(llmobs):
    """Test that llmobs_trace_id is written to the local root span's tags for the backend processor."""
    with llmobs._instance.tracer.trace("fastapi.request") as root:
        with llmobs.workflow("chat-workflow") as wf:
            wf_trace_id = get_llmobs_trace_id(wf)

    # The local root (fastapi.request) should have llmobs_trace_id set as a tag
    assert root.get_tag("llmobs_trace_id") is not None
    # It should match the workflow span's llmobs_trace_id
    assert root.get_tag("llmobs_trace_id") == wf_trace_id
    # llmobs_parent_id should be the workflow span's span_id
    assert root.get_tag("llmobs_parent_id") == str(wf.span_id)


def test_llmobs_trace_id_on_local_root_with_non_llm_child_spans(llmobs):
    """Test that non-LLM child spans share the local root that has llmobs_trace_id."""
    with llmobs._instance.tracer.trace("fastapi.request") as root:
        with llmobs.workflow("chat-workflow") as wf:
            wf_trace_id = get_llmobs_trace_id(wf)
            # Simulate OTel-bridged span (non-LLM, child of root)
            child = llmobs._instance.tracer.start_span("gen_ai.chat", child_of=root, activate=False)
            child._set_attribute("gen_ai.system", "aws.bedrock")
            child.finish()

    # Root should have llmobs_trace_id
    assert root.get_tag("llmobs_trace_id") is not None
    # The child span shares the same local root
    assert child._local_root is root
    # So the processor will find llmobs_trace_id on the root span in the same payload
    assert root.get_tag("llmobs_trace_id") == wf_trace_id


def test_llmobs_trace_id_not_overwritten_by_sibling_workflows(llmobs):
    """Test that sibling LLMObs workflows don't overwrite each other's trace ID on the local root."""
    with llmobs._instance.tracer.trace("parent_non_llm") as root:
        with llmobs.workflow("first_child") as first:
            first_trace_id = get_llmobs_trace_id(first)
        with llmobs.workflow("second_child") as second:
            second_trace_id = get_llmobs_trace_id(second)

    # The local root should have the first child's trace ID (first-wins)
    assert root.get_tag("llmobs_trace_id") is not None
    assert root.get_tag("llmobs_trace_id") == first_trace_id
    # The two workflows should have different trace IDs
    assert first_trace_id != second_trace_id


def test_llmobs_submitted_tag_set_on_apm_span(llmobs):
    """Test that _dd.llmobs.submitted is set on the APM span when SDK submits an LLMObs event."""
    with llmobs.workflow("my-workflow") as span:
        pass

    assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"


def test_llmobs_submitted_tag_not_set_without_llmobs(llmobs):
    """Test that _dd.llmobs.submitted is NOT set on regular APM spans."""
    with llmobs._instance.tracer.trace("regular_span") as span:
        pass

    assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None


class TestAPMShadowTags:
    """Test that _apply_shadow_metrics sets shadow token tags on APM spans."""

    @staticmethod
    def _make_integration(llmobs_enabled=False):
        """Create a minimal BaseLLMIntegration for testing _apply_shadow_metrics."""
        from unittest.mock import MagicMock

        integration = MagicMock(spec=BaseLLMIntegration)
        integration.llmobs_enabled = llmobs_enabled
        integration._apply_shadow_metrics = BaseLLMIntegration._apply_shadow_metrics.__get__(integration)
        return integration

    def test_shadow_metrics_on_llm_span(self, tracer):
        """Shadow token metrics and span_kind tag are set for llm spans."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
            integration._apply_shadow_metrics(span, metrics, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY) == 10
        assert span.get_metric(LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY) == 20
        assert span.get_metric(LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY) == 30
        assert span.get_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY) == "llm"

    def test_shadow_metrics_on_embedding_span(self, tracer):
        """Shadow token metrics are set for embedding spans."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {"input_tokens": 15, "total_tokens": 15}
            integration._apply_shadow_metrics(span, metrics, "embedding")

        assert span.get_metric(LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY) == 15
        assert span.get_metric(LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY) is None
        assert span.get_metric(LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY) == 15
        assert span.get_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY) == "embedding"

    def test_shadow_metrics_not_set_on_non_llm_spans(self, tracer):
        """Token metrics are not set on workflow spans, but span_kind and enabled are."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
            integration._apply_shadow_metrics(span, metrics, "workflow")

        assert span.get_metric(LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY) is None
        assert span.get_metric(LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY) is None
        assert span.get_metric(LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY) is None
        assert span.get_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY) == "workflow"
        assert span.get_metric(LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY) == 0

    def test_shadow_metrics_partial(self, tracer):
        """Only present token metrics get shadow tags."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {"input_tokens": 5}
            integration._apply_shadow_metrics(span, metrics, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY) == 5
        assert span.get_metric(LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY) is None
        assert span.get_metric(LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY) is None
        assert span.get_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY) == "llm"

    def test_shadow_metrics_zero_values(self, tracer):
        """Zero token values are set (not treated as falsy)."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            integration._apply_shadow_metrics(span, metrics, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY) == 0
        assert span.get_metric(LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY) == 0
        assert span.get_metric(LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY) == 0

    def test_shadow_metrics_none_metrics(self, tracer):
        """No token shadow tags set when metrics is None."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            integration._apply_shadow_metrics(span, None, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY) is None
        assert span.get_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY) == "llm"

    def test_shadow_metrics_embedding_span_kind(self, tracer):
        """Embedding span kind is set as 'embedding'."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            integration._apply_shadow_metrics(span, {"input_tokens": 5, "total_tokens": 5}, "embedding")

        assert span.get_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY) == "embedding"

    def test_shadow_metrics_enabled_flag_true(self, tracer):
        """_dd.llmobs.enabled is 1 when llmobs_enabled=True."""
        integration = self._make_integration(llmobs_enabled=True)

        with tracer.trace("test") as span:
            integration._apply_shadow_metrics(span, {"input_tokens": 10, "total_tokens": 10}, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY) == 1

    def test_shadow_metrics_enabled_flag_false(self, tracer):
        """_dd.llmobs.enabled is 0 when llmobs_enabled=False (default)."""
        integration = self._make_integration(llmobs_enabled=False)

        with tracer.trace("test") as span:
            integration._apply_shadow_metrics(span, {"input_tokens": 10, "total_tokens": 10}, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY) == 0

    def test_shadow_metrics_cache_tokens_on_llm_span(self, tracer):
        """Cache token shadow metrics are forwarded for llm spans."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "cache_read_input_tokens": 7,
                "cache_write_input_tokens": 4,
            }
            integration._apply_shadow_metrics(span, metrics, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY) == 7
        assert span.get_metric(LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY) == 4

    def test_shadow_metrics_cache_tokens_absent_when_not_extracted(self, tracer):
        """Cache shadow metrics are absent when the integration didn't extract them."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            integration._apply_shadow_metrics(span, {"input_tokens": 10, "total_tokens": 10}, "llm")

        assert span.get_metric(LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY) is None
        assert span.get_metric(LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY) is None

    def test_shadow_metrics_cache_tokens_not_set_on_workflow_span(self, tracer):
        """Cache shadow metrics are not set on non-llm/embedding spans."""
        integration = self._make_integration()

        with tracer.trace("test") as span:
            metrics = {"cache_read_input_tokens": 5, "cache_write_input_tokens": 3}
            integration._apply_shadow_metrics(span, metrics, "workflow")

        assert span.get_metric(LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY) is None
        assert span.get_metric(LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY) is None


def test_no_llmobs_trace_id_without_llmobs_context(llmobs):
    """Test that llmobs_trace_id is NOT written when there are no LLMObs spans."""
    with llmobs._instance.tracer.trace("regular_span") as span:
        with llmobs._instance.tracer.trace("child_span"):
            pass

    assert not span._has_attribute("llmobs_trace_id")


@pytest.mark.parametrize("llmobs_env", [{"DD_APM_TRACING_ENABLED": "false"}])
def test_llmobs_events_still_sent_if_apm_tracing_disabled(llmobs, llmobs_events, tracer, llmobs_env):
    from tests.utils import DummyWriter

    dummy_writer = DummyWriter()
    tracer._span_aggregator.writer = dummy_writer

    with tracer.trace("apm_span") as apm_span:
        apm_span.set_tag("operation", "test")

    # Create an LLMObs span (should be sent to LLMObs but not APM)
    with llmobs.llm(model_name="test-model") as llm_span:
        llmobs.annotate(llm_span, input_data="test input", output_data="test output")

    # Check that no APM traces were sent to the writer
    assert len(dummy_writer.traces) == 0, "APM traces should be dropped when DD_APM_TRACING_ENABLED=false"

    # But LLMObs events should still be sent
    assert len(llmobs_events) == 1
    llm_event = llmobs_events[0]
    assert llm_event["meta"]["span"]["kind"] == "llm"
    assert llm_event["meta"]["model_name"] == "test-model"
