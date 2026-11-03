import json
from unittest import mock

import pytest

from ddtrace.llmobs._integrations.crewai import _listener_condition
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_span_name
from tests.contrib.crewai.utils import CREWAI_VERSION
from tests.contrib.crewai.utils import FailingTool
from tests.contrib.crewai.utils import MaxTool
from tests.contrib.crewai.utils import fun_fact_text
from tests.contrib.crewai.utils import requires_akickoff
from tests.llmobs._utils import _assert_span_link
from tests.llmobs._utils import assert_llmobs_span_data


try:
    from crewai.constants import DEFAULT_LLM_MODEL
except ImportError:  # crewai<1.0
    from crewai.cli.constants import DEFAULT_LLM_MODEL


# crewai 1.x keeps a tool's own description instead of generating one that lists its name and arguments.
AVERAGE_TOOL_DESCRIPTION = (
    "This tool returns the average of a list of numbers."
    if CREWAI_VERSION >= (1, 0, 0)
    else "Tool Name: Average Calculator\nTool Arguments: {'entries': {'description': None, 'type': 'list[float]'}}\n"
    "Tool Description: This tool returns the average of a list of numbers."
)

AGES = {"ages": [10, 12, 14, 16, 18]}


AGENT_TO_EXPECTED_AGENT_MANIFEST = {
    "Senior Research Scientist": {
        "framework": "CrewAI",
        "name": "Senior Research Scientist",
        "goal": "Uncover cutting-edge developments in AI",
        "backstory": "You're a seasoned researcher with a knack for uncovering the latest developments in AI. "
        "Known for your ability to find the most relevant information and present it in a clear "
        "and concise manner.",
        "model": DEFAULT_LLM_MODEL,
        "model_settings": {"max_tokens": None, "temperature": None},
        "handoffs": {"allow_delegation": False},
        "code_execution_permissions": {"code_execution_mode": "safe"},
        "max_iterations": 25,
        "tools": [],
    },
    "AI Reporting Analyst": {
        "framework": "CrewAI",
        "name": "AI Reporting Analyst",
        "goal": "Create detailed reports based on AI data analysis and research findings",
        "backstory": "You're a meticulous analyst with a keen eye for detail. You're known for your ability to turn "
        "complex data into clear and concise reports, making it easy for others to understand and act on the "
        "information you provide.",
        "model": DEFAULT_LLM_MODEL,
        "model_settings": {"max_tokens": None, "temperature": None},
        "handoffs": {"allow_delegation": False},
        "code_execution_permissions": {"code_execution_mode": "safe"},
        "max_iterations": 25,
        "tools": [],
    },
    "Python Data Analyst": {
        "framework": "CrewAI",
        "name": "Python Data Analyst",
        "goal": "Analyze data and provide insights using Python",
        "backstory": "You are an experienced data analyst with strong Python skills.",
        "model": DEFAULT_LLM_MODEL,
        "model_settings": {"max_tokens": None, "temperature": None},
        "handoffs": {"allow_delegation": False},
        "code_execution_permissions": {"code_execution_mode": "safe"},
        "max_iterations": 25,
        "tools": [
            {
                "name": "Average Calculator",
                "description": AVERAGE_TOOL_DESCRIPTION,
            }
        ],
    },
    "Tour Guide": {
        "framework": "CrewAI",
        "name": "Tour Guide",
        "goal": "Recommend fun activities for a group of humans.",
        "backstory": "You are a tour guide with a passion for finding the best activities for groups of people.",
        "model": DEFAULT_LLM_MODEL,
        "model_settings": {"max_tokens": None, "temperature": None},
        "handoffs": {"allow_delegation": False},
        "code_execution_permissions": {"code_execution_mode": "safe"},
        "max_iterations": 25,
        "tools": [],
    },
}


# Common kwargs for non-agent crew/task spans. ``span_links`` is asserted separately
# via the link helpers below so it doesn't appear here. ``parent_id=mock.ANY`` mirrors
# the previous behaviour: async-task spans don't have a deterministic in-process parent.
EXPECTED_BASE_SPAN_DATA = {
    "input_value": mock.ANY,
    "output_value": mock.ANY,
    "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
    "parent_id": mock.ANY,
}


def _expected_agent_span_data(role):
    return {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": {"_dd": {"agent_manifest": AGENT_TO_EXPECTED_AGENT_MANIFEST[role]}},
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
        "parent_id": mock.ANY,
    }


def _expected_tool_span_data():
    return {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
    }


def _llmobs_name(span):
    """Return the LLMObs span name as projected to the wire (matches event['name'])."""
    return get_llmobs_span_name(span) or span.name


def _ordered_spans(test_spans):
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda s: s.start_ns)
    return spans


def _assert_basic_crew_events(spans):
    assert len(spans) == 5
    expected_kinds = ("workflow", "task", "agent", "task", "agent")
    for span, kind in zip(spans, expected_kinds):
        kwargs = _expected_agent_span_data(_llmobs_name(span)) if kind == "agent" else EXPECTED_BASE_SPAN_DATA
        assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind=kind, **kwargs)
    # assert parent_id chain: workflow -> task -> agent
    assert get_llmobs_parent_id(spans[1]) == str(spans[0].span_id)  # task -> workflow
    assert get_llmobs_parent_id(spans[2]) == str(spans[1].span_id)  # agent -> task
    assert get_llmobs_parent_id(spans[3]) == str(spans[0].span_id)  # task -> workflow
    assert get_llmobs_parent_id(spans[4]) == str(spans[3].span_id)  # agent -> task


def _assert_basic_crew_links(spans):
    # span links for crew -> task
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[1], spans[3], "output", "input")
    _assert_span_link(spans[3], spans[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(spans[1], spans[2], "input", "input")
    _assert_span_link(spans[2], spans[1], "output", "output")
    _assert_span_link(spans[3], spans[4], "input", "input")
    _assert_span_link(spans[4], spans[3], "output", "output")


def _assert_tool_crew_events(spans):
    assert len(spans) == 4
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_BASE_SPAN_DATA)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[1]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[2]), span_kind="agent", **_expected_agent_span_data(_llmobs_name(spans[2]))
    )
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[3]), span_kind="tool", **_expected_tool_span_data())
    # assert parent_id chain: workflow -> task -> agent -> tool
    assert get_llmobs_parent_id(spans[1]) == str(spans[0].span_id)  # task -> workflow
    assert get_llmobs_parent_id(spans[2]) == str(spans[1].span_id)  # agent -> task
    assert get_llmobs_parent_id(spans[3]) == str(spans[2].span_id)  # tool -> agent


def _assert_tool_crew_links(spans):
    # span links for crew -> task
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[1], spans[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(spans[1], spans[2], "input", "input")
    _assert_span_link(spans[2], spans[1], "output", "output")


def _assert_async_crew_events(spans):
    assert len(spans) == 6
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_BASE_SPAN_DATA)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[1]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[2]), span_kind="agent", **_expected_agent_span_data(_llmobs_name(spans[2]))
    )
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[3]), span_kind="tool", **_expected_tool_span_data())
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[4]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[5]), span_kind="agent", **_expected_agent_span_data(_llmobs_name(spans[5]))
    )
    # assert parent_id chain: workflow -> task -> agent, workflow -> task -> agent
    assert get_llmobs_parent_id(spans[1]) == str(spans[0].span_id)  # task -> workflow
    assert get_llmobs_parent_id(spans[2]) == str(spans[1].span_id)  # agent -> task
    assert get_llmobs_parent_id(spans[4]) == str(spans[0].span_id)  # task -> workflow
    assert get_llmobs_parent_id(spans[5]) == str(spans[4].span_id)  # agent -> task


def _assert_async_crew_links(spans):
    # span links for crew -> task
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[1], spans[4], "output", "input")
    _assert_span_link(spans[4], spans[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(spans[1], spans[2], "input", "input")
    _assert_span_link(spans[2], spans[1], "output", "output")
    _assert_span_link(spans[4], spans[5], "input", "input")
    _assert_span_link(spans[5], spans[4], "output", "output")


def _assert_hierarchical_crew_events(spans):
    if CREWAI_VERSION >= (1, 0, 0):
        # The crewai 1.x recording delegates only the first task, and the coworker answers without its tool.
        expected_span_kinds = ("workflow", "task", "agent", "task", "agent", "tool", "agent", "task", "agent")
        assert len(spans) == len(expected_span_kinds)
        for span, kind in zip(spans, expected_span_kinds):
            assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind=kind, **EXPECTED_BASE_SPAN_DATA)
        return
    assert len(spans) == 12
    expected_span_kinds = (
        "workflow",
        "task",
        "agent",
        "task",
        "agent",
        "tool",
        "agent",
        None,
        "task",
        "agent",
        "tool",
        "agent",
    )
    for span, kind in zip(spans, expected_span_kinds):
        if kind is None:  # Not expecting any span links for this tool span
            assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="tool", **_expected_tool_span_data())
            continue
        assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind=kind, **EXPECTED_BASE_SPAN_DATA)


def _assert_hierarchical_crew_links(spans):
    if CREWAI_VERSION >= (1, 0, 0):
        _assert_span_link(spans[0], spans[1], "input", "input")
        _assert_span_link(spans[1], spans[3], "output", "input")
        _assert_span_link(spans[3], spans[7], "output", "input")
        _assert_span_link(spans[7], spans[0], "output", "output")
        for task_or_tool, agent in ((1, 2), (3, 4), (5, 6), (7, 8)):
            _assert_span_link(spans[task_or_tool], spans[agent], "input", "input")
            _assert_span_link(spans[agent], spans[task_or_tool], "output", "output")
        return
    # span links for crew -> task
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[1], spans[3], "output", "input")
    _assert_span_link(spans[3], spans[8], "output", "input")
    _assert_span_link(spans[8], spans[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(spans[1], spans[2], "input", "input")
    _assert_span_link(spans[2], spans[1], "output", "output")
    _assert_span_link(spans[3], spans[4], "input", "input")
    _assert_span_link(spans[4], spans[3], "output", "output")
    _assert_span_link(spans[5], spans[6], "input", "input")
    _assert_span_link(spans[6], spans[5], "output", "output")
    _assert_span_link(spans[8], spans[9], "input", "input")
    _assert_span_link(spans[9], spans[8], "output", "output")
    _assert_span_link(spans[10], spans[11], "input", "input")
    _assert_span_link(spans[11], spans[10], "output", "output")


def _assert_simple_flow_events(spans):
    assert len(spans) == 3
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_BASE_SPAN_DATA)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[1]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)
    assert get_llmobs_output_value(spans[1]) == "New York City"
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[2]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)
    if CREWAI_VERSION >= (0, 119, 0):  # Tracking I/O and state management only available CrewAI >=0.119.0
        input_val = json.loads(get_llmobs_input_value(spans[0]))
        assert input_val == {"continent": "North America"}
        assert get_llmobs_output_value(spans[0]) == fun_fact_text
        input_val = json.loads(get_llmobs_input_value(spans[1]))
        assert input_val["args"] == []
        assert input_val["kwargs"] == {}
        assert input_val["flow_state"] == {"id": mock.ANY, "continent": "North America"}
        input_val = json.loads(get_llmobs_input_value(spans[2]))
        assert input_val["args"] == ["New York City"]
        assert input_val["kwargs"] == {}
        assert input_val["flow_state"] == {"id": mock.ANY, "continent": "North America"}
        assert get_llmobs_output_value(spans[2]) == fun_fact_text


def _assert_simple_flow_links(spans):
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[1], spans[2], "output", "input")
    _assert_span_link(spans[2], spans[0], "output", "output")


def _assert_complex_flow_events(spans):
    assert len(spans) == 6
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_BASE_SPAN_DATA)
    for i in range(1, 6):
        assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[i]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)


def _assert_complex_flow_links(spans):
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[0], spans[2], "input", "input")
    _assert_span_link(spans[5], spans[0], "output", "output")

    _assert_span_link(spans[1], spans[3], "output", "input")
    _assert_span_link(spans[1], spans[4], "output", "input")
    _assert_span_link(spans[1], spans[5], "output", "input")

    _assert_span_link(spans[2], spans[5], "output", "input")
    _assert_span_link(spans[3], spans[5], "output", "input")
    _assert_span_link(spans[4], spans[5], "output", "input")


def _assert_router_flow_events(spans):
    assert len(spans) == 4
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_BASE_SPAN_DATA)
    for i in range(1, 4):
        assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[i]), span_kind="task", **EXPECTED_BASE_SPAN_DATA)


def _assert_router_flow_links(spans):
    _assert_span_link(spans[0], spans[1], "input", "input")
    _assert_span_link(spans[1], spans[2], "output", "input")
    _assert_span_link(spans[2], spans[3], "output", "input")
    _assert_span_link(spans[3], spans[0], "output", "output")


def test_basic_crew(crewai, basic_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff(inputs={"topic": "AI"})
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


def test_basic_crew_for_each(crewai, basic_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff_for_each(inputs=[{"topic": "AI"}])
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


async def test_basic_crew_async(crewai, basic_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_async(inputs={"topic": "AI"})
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


async def test_basic_crew_async_for_each(crewai, basic_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_for_each_async(inputs=[{"topic": "AI"}])
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


def test_crew_with_tool(crewai, tool_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


def test_crew_with_tool_for_each(crewai, tool_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


async def test_crew_with_tool_async(crewai, tool_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


async def test_crew_with_tool_async_for_each(crewai, tool_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


def test_async_crew(crewai, async_exec_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


def test_async_crew_for_each(crewai, async_exec_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


async def test_async_crew_async(crewai, async_exec_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


async def test_async_crew_async_for_each(crewai, async_exec_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


def test_conditional_crew(crewai, conditional_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        conditional_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


async def test_conditional_crew_async(crewai, conditional_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await conditional_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


def test_hierarchical_crew(crewai, hierarchical_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


def test_hierarchical_crew_for_each(crewai, hierarchical_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


async def test_hierarchical_crew_async(crewai, hierarchical_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


async def test_hierarchical_crew_async_for_each(crewai, hierarchical_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


def test_simple_flow(crewai, simple_flow, crewai_llmobs, test_spans):
    simple_flow.kickoff(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_simple_flow_events(spans)
    _assert_simple_flow_links(spans)


async def test_simple_flow_async(crewai, simple_flow_async, crewai_llmobs, test_spans):
    await simple_flow_async.kickoff_async(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_simple_flow_events(spans)
    _assert_simple_flow_links(spans)


def test_complex_flow(crewai, complex_flow, crewai_llmobs, test_spans):
    complex_flow.kickoff(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_complex_flow_events(spans)
    _assert_complex_flow_links(spans)


async def test_complex_flow_async(crewai, complex_flow_async, crewai_llmobs, test_spans):
    await complex_flow_async.kickoff_async(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_complex_flow_events(spans)
    _assert_complex_flow_links(spans)


def test_router_flow(crewai, router_flow, crewai_llmobs, test_spans):
    router_flow.kickoff()
    spans = _ordered_spans(test_spans)
    _assert_router_flow_events(spans)
    _assert_router_flow_links(spans)


async def test_router_flow_async(crewai, router_flow_async, crewai_llmobs, test_spans):
    await router_flow_async.kickoff_async()
    spans = _ordered_spans(test_spans)
    _assert_router_flow_events(spans)
    _assert_router_flow_links(spans)


# Only the stored format of the installed crewai version runs through the flow tests above; CI runs crewai 0.102 and
# the latest 1.x, so the crewai 1.0 to 1.14 format of and_/or_ listeners is covered here.
@pytest.mark.parametrize(
    "condition,expected",
    [
        (("AND", ["a", "b"]), ("AND", ["a", "b"])),
        ("a", ("OR", ["a"])),
        ({"type": "AND", "conditions": ["a", {"type": "OR", "conditions": ["b", "c"]}]}, ("AND", ["a", "b", "c"])),
        ({"and": ["a", {"or": ["b", "c"]}]}, ("AND", ["a", "b", "c"])),
        ({"or": ["a", "b"]}, ("OR", ["a", "b"])),
    ],
    ids=["tuple", "method_name", "type_conditions", "and_dict", "or_dict"],
)
def test_listener_condition(condition, expected):
    assert _listener_condition(condition) == expected


@requires_akickoff
@pytest.mark.parametrize(
    "crew_fixture,cassette,inputs,assert_events,assert_links",
    [
        ("basic_crew", "test_basic_crew.yaml", {"topic": "AI"}, _assert_basic_crew_events, _assert_basic_crew_links),
        ("tool_crew", "test_crew_with_tool.yaml", AGES, _assert_tool_crew_events, _assert_tool_crew_links),
        (
            "async_exec_crew",
            "test_crew_with_async_tasks.yaml",
            AGES,
            _assert_async_crew_events,
            _assert_async_crew_links,
        ),
    ],
    ids=["basic", "tool", "async_tasks"],
)
async def test_crew_akickoff(
    crewai, request, request_vcr, crewai_llmobs, test_spans, crew_fixture, cassette, inputs, assert_events, assert_links
):
    crew = request.getfixturevalue(crew_fixture)
    with request_vcr.use_cassette(cassette):
        await crew.akickoff(inputs=inputs)
    spans = _ordered_spans(test_spans)
    assert_events(spans)
    assert_links(spans)


@requires_akickoff
async def test_crew_akickoff_error(crewai, failing_llm_crew, request_vcr, crewai_llmobs, test_spans):
    with request_vcr.use_cassette("test_crew_akickoff_error.yaml"):
        with pytest.raises(Exception):
            await failing_llm_crew.akickoff()
    spans = _ordered_spans(test_spans)
    assert [span.error for span in spans] == [1, 1, 1]
    for span, kind in zip(spans, ("workflow", "task", "agent")):
        assert _get_llmobs_data_metastruct(span)["meta"]["span"]["kind"] == kind
        assert "Invalid 'temperature'" in _get_llmobs_data_metastruct(span)["meta"]["error"]["message"]


@requires_akickoff
async def test_simple_flow_akickoff(crewai, simple_flow_async, crewai_llmobs, test_spans):
    await simple_flow_async.akickoff(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_simple_flow_events(spans)
    _assert_simple_flow_links(spans)


def test_tool_run(crewai, crewai_llmobs, test_spans):
    MaxTool().run(entries=[10, 12, 14])
    spans = _ordered_spans(test_spans)
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="tool",
        name="Max Calculator",
        input_value=json.dumps({"entries": [10, 12, 14]}),
        output_value="14",
        metadata={"description": "This tool returns the largest of a list of numbers."},
        tags={"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
    )


@pytest.mark.parametrize("use_ainvoke", [False, True], ids=["run", "ainvoke"])
async def test_tool_error(crewai, crewai_llmobs, test_spans, use_ainvoke):
    with pytest.raises(ValueError):
        if use_ainvoke:
            await FailingTool().to_structured_tool().ainvoke(input={"entries": [10, 12, 14]})
        else:
            FailingTool().run(entries=[10, 12, 14])
    spans = _ordered_spans(test_spans)
    assert len(spans) == 1
    assert spans[0].error == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="tool",
        name="Failing Calculator",
        input_value=json.dumps({"entries": [10, 12, 14]}),
        error=mock.ANY,
        tags={"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
    )
