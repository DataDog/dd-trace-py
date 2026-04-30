import json

import crewai
import mock
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.contrib.crewai.utils import fun_fact_text
from tests.llmobs._utils import _assert_span_link
from tests.llmobs._utils import assert_llmobs_span_data


CREWAI_VERSION = parse_version(getattr(crewai, "__version__", "0.0.0"))


LLMOBS_GLOBAL_CONFIG = dict(
    _dd_api_key="<not-a-real-api_key>",
    _llmobs_ml_app="<ml-app-name>",
    _llmobs_enabled=True,
    _llmobs_sample_rate=1.0,
    service="tests.contrib.crewai",
)


AGENT_TO_EXPECTED_AGENT_MANIFEST = {
    "Senior Research Scientist": {
        "framework": "CrewAI",
        "name": "Senior Research Scientist",
        "goal": "Uncover cutting-edge developments in AI",
        "backstory": "You're a seasoned researcher with a knack for uncovering the latest developments in AI. "
        "Known for your ability to find the most relevant information and present it in a clear "
        "and concise manner.",
        "model": "gpt-4o-mini",
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
        "model": "gpt-4o-mini",
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
        "model": "gpt-4o-mini",
        "model_settings": {"max_tokens": None, "temperature": None},
        "handoffs": {"allow_delegation": False},
        "code_execution_permissions": {"code_execution_mode": "safe"},
        "max_iterations": 25,
        "tools": [
            {
                "name": "Average Calculator",
                "description": "Tool Name: Average Calculator\nTool Arguments: {'entries': {'description': None, "
                "'type': 'list'}}\nTool Description: This tool returns the average of a list of numbers.",
            }
        ],
    },
    "Tour Guide": {
        "framework": "CrewAI",
        "name": "Tour Guide",
        "goal": "Recommend fun activities for a group of humans.",
        "backstory": "You are a tour guide with a passion for finding the best activities for groups of people.",
        "model": "gpt-4o-mini",
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
EXPECTED_SPAN_KWARGS = {
    "input_value": mock.ANY,
    "output_value": mock.ANY,
    "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
    "parent_id": mock.ANY,
}


def _expected_agent_kwargs(role):
    return {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": {"_dd": {"agent_manifest": AGENT_TO_EXPECTED_AGENT_MANIFEST[role]}},
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
        "parent_id": mock.ANY,
    }


def _expected_tool_kwargs():
    return {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>", "integration": "crewai"},
    }


def _llmobs_name(span):
    """Return the LLMObs span name as projected to the wire (matches event['name'])."""
    return _get_llmobs_data_metastruct(span).get("name") or span.name


def _link_event(span):
    """Build a minimal dict that ``_assert_span_link`` can consume.

    ``_assert_span_link`` only reads ``span_id`` and ``span_links`` from the dicts it's
    given, so we splice them out of the metastruct payload and the underlying span.
    """
    metastruct = _get_llmobs_data_metastruct(span)
    return {"span_id": str(span.span_id), "span_links": metastruct.get("span_links") or []}


def _ordered_spans(test_spans):
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    spans.sort(key=lambda s: s.start_ns)
    return spans


def _assert_basic_crew_events(spans):
    assert len(spans) == 5
    expected_kinds = ("workflow", "task", "agent", "task", "agent")
    for span, kind in zip(spans, expected_kinds):
        kwargs = _expected_agent_kwargs(_llmobs_name(span)) if kind == "agent" else EXPECTED_SPAN_KWARGS
        assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind=kind, **kwargs)
    # assert parent_id chain: workflow -> task -> agent
    assert _get_llmobs_data_metastruct(spans[1])["parent_id"] == str(spans[0].span_id)  # task -> workflow
    assert _get_llmobs_data_metastruct(spans[2])["parent_id"] == str(spans[1].span_id)  # agent -> task
    assert _get_llmobs_data_metastruct(spans[3])["parent_id"] == str(spans[0].span_id)  # task -> workflow
    assert _get_llmobs_data_metastruct(spans[4])["parent_id"] == str(spans[3].span_id)  # agent -> task


def _assert_basic_crew_links(spans):
    events = [_link_event(s) for s in spans]
    # span links for crew -> task
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[1], events[3], "output", "input")
    _assert_span_link(events[3], events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(events[1], events[2], "input", "input")
    _assert_span_link(events[2], events[1], "output", "output")
    _assert_span_link(events[3], events[4], "input", "input")
    _assert_span_link(events[4], events[3], "output", "output")


def _assert_tool_crew_events(spans):
    assert len(spans) == 4
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_SPAN_KWARGS)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[1]), span_kind="task", **EXPECTED_SPAN_KWARGS)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[2]), span_kind="agent", **_expected_agent_kwargs(_llmobs_name(spans[2]))
    )
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[3]), span_kind="tool", **_expected_tool_kwargs())
    # assert parent_id chain: workflow -> task -> agent -> tool
    assert _get_llmobs_data_metastruct(spans[1])["parent_id"] == str(spans[0].span_id)  # task -> workflow
    assert _get_llmobs_data_metastruct(spans[2])["parent_id"] == str(spans[1].span_id)  # agent -> task
    assert _get_llmobs_data_metastruct(spans[3])["parent_id"] == str(spans[2].span_id)  # tool -> agent


def _assert_tool_crew_links(spans):
    events = [_link_event(s) for s in spans]
    # span links for crew -> task
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[1], events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(events[1], events[2], "input", "input")
    _assert_span_link(events[2], events[1], "output", "output")


def _assert_async_crew_events(spans):
    assert len(spans) == 6
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_SPAN_KWARGS)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[1]), span_kind="task", **EXPECTED_SPAN_KWARGS)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[2]), span_kind="agent", **_expected_agent_kwargs(_llmobs_name(spans[2]))
    )
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[3]), span_kind="tool", **_expected_tool_kwargs())
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[4]), span_kind="task", **EXPECTED_SPAN_KWARGS)
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[5]), span_kind="agent", **_expected_agent_kwargs(_llmobs_name(spans[5]))
    )
    # assert parent_id chain: workflow -> task -> agent, workflow -> task -> agent
    assert _get_llmobs_data_metastruct(spans[1])["parent_id"] == str(spans[0].span_id)  # task -> workflow
    assert _get_llmobs_data_metastruct(spans[2])["parent_id"] == str(spans[1].span_id)  # agent -> task
    assert _get_llmobs_data_metastruct(spans[4])["parent_id"] == str(spans[0].span_id)  # task -> workflow
    assert _get_llmobs_data_metastruct(spans[5])["parent_id"] == str(spans[4].span_id)  # agent -> task


def _assert_async_crew_links(spans):
    events = [_link_event(s) for s in spans]
    # span links for crew -> task
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[1], events[4], "output", "input")
    _assert_span_link(events[4], events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(events[1], events[2], "input", "input")
    _assert_span_link(events[2], events[1], "output", "output")
    _assert_span_link(events[4], events[5], "input", "input")
    _assert_span_link(events[5], events[4], "output", "output")


def _assert_hierarchical_crew_events(spans):
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
            assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="tool", **_expected_tool_kwargs())
            continue
        assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind=kind, **EXPECTED_SPAN_KWARGS)


def _assert_hierarchical_crew_links(spans):
    events = [_link_event(s) for s in spans]
    # span links for crew -> task
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[1], events[3], "output", "input")
    _assert_span_link(events[3], events[8], "output", "input")
    _assert_span_link(events[8], events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(events[1], events[2], "input", "input")
    _assert_span_link(events[2], events[1], "output", "output")
    _assert_span_link(events[3], events[4], "input", "input")
    _assert_span_link(events[4], events[3], "output", "output")
    _assert_span_link(events[5], events[6], "input", "input")
    _assert_span_link(events[6], events[5], "output", "output")
    _assert_span_link(events[8], events[9], "input", "input")
    _assert_span_link(events[9], events[8], "output", "output")
    _assert_span_link(events[10], events[11], "input", "input")
    _assert_span_link(events[11], events[10], "output", "output")


def _assert_simple_flow_events(spans):
    assert len(spans) == 3
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_SPAN_KWARGS)
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[1]), span_kind="task", **EXPECTED_SPAN_KWARGS)
    assert _get_llmobs_data_metastruct(spans[1])["meta"]["output"]["value"] == "New York City"
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[2]), span_kind="task", **EXPECTED_SPAN_KWARGS)
    if CREWAI_VERSION >= (0, 119, 0):  # Tracking I/O and state management only available CrewAI >=0.119.0
        meta_0 = _get_llmobs_data_metastruct(spans[0])
        meta_1 = _get_llmobs_data_metastruct(spans[1])
        meta_2 = _get_llmobs_data_metastruct(spans[2])
        input_val = json.loads(meta_0["meta"]["input"]["value"])
        assert input_val == {"continent": "North America"}
        assert meta_0["meta"]["output"]["value"] == fun_fact_text
        input_val = json.loads(meta_1["meta"]["input"]["value"])
        assert input_val["args"] == []
        assert input_val["kwargs"] == {}
        assert input_val["flow_state"] == {"id": mock.ANY, "continent": "North America"}
        input_val = json.loads(meta_2["meta"]["input"]["value"])
        assert input_val["args"] == ["New York City"]
        assert input_val["kwargs"] == {}
        assert input_val["flow_state"] == {"id": mock.ANY, "continent": "North America"}
        assert meta_2["meta"]["output"]["value"] == fun_fact_text


def _assert_simple_flow_links(spans):
    events = [_link_event(s) for s in spans]
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[1], events[2], "output", "input")
    _assert_span_link(events[2], events[0], "output", "output")


def _assert_complex_flow_events(spans):
    assert len(spans) == 6
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_SPAN_KWARGS)
    for i in range(1, 6):
        assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[i]), span_kind="task", **EXPECTED_SPAN_KWARGS)


def _assert_complex_flow_links(spans):
    events = [_link_event(s) for s in spans]
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[0], events[2], "input", "input")
    _assert_span_link(events[5], events[0], "output", "output")

    _assert_span_link(events[1], events[3], "output", "input")
    _assert_span_link(events[1], events[4], "output", "input")
    _assert_span_link(events[1], events[5], "output", "input")

    _assert_span_link(events[2], events[5], "output", "input")
    _assert_span_link(events[3], events[5], "output", "input")
    _assert_span_link(events[4], events[5], "output", "input")


def _assert_router_flow_events(spans):
    assert len(spans) == 4
    assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[0]), span_kind="workflow", **EXPECTED_SPAN_KWARGS)
    for i in range(1, 4):
        assert_llmobs_span_data(_get_llmobs_data_metastruct(spans[i]), span_kind="task", **EXPECTED_SPAN_KWARGS)


def _assert_router_flow_links(spans):
    events = [_link_event(s) for s in spans]
    _assert_span_link(events[0], events[1], "input", "input")
    _assert_span_link(events[1], events[2], "output", "input")
    _assert_span_link(events[2], events[3], "output", "input")
    _assert_span_link(events[3], events[0], "output", "output")


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_basic_crew(crewai, basic_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff(inputs={"topic": "AI"})
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_basic_crew_for_each(crewai, basic_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff_for_each(inputs=[{"topic": "AI"}])
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_basic_crew_async(crewai, basic_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_async(inputs={"topic": "AI"})
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_basic_crew_async_for_each(crewai, basic_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_for_each_async(inputs=[{"topic": "AI"}])
    spans = _ordered_spans(test_spans)
    _assert_basic_crew_events(spans)
    _assert_basic_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_crew_with_tool(crewai, tool_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_crew_with_tool_for_each(crewai, tool_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_crew_with_tool_async(crewai, tool_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_crew_with_tool_async_for_each(crewai, tool_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_tool_crew_events(spans)
    _assert_tool_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_async_crew(crewai, async_exec_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_async_crew_for_each(crewai, async_exec_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_async_crew_async(crewai, async_exec_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_async_crew_async_for_each(crewai, async_exec_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_conditional_crew(crewai, conditional_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        conditional_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_conditional_crew_async(crewai, conditional_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await conditional_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_async_crew_events(spans)
    _assert_async_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_hierarchical_crew(crewai, hierarchical_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_hierarchical_crew_for_each(crewai, hierarchical_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_hierarchical_crew_async(crewai, hierarchical_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_hierarchical_crew_async_for_each(crewai, hierarchical_crew, request_vcr, test_spans):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = _ordered_spans(test_spans)
    _assert_hierarchical_crew_events(spans)
    _assert_hierarchical_crew_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_simple_flow(crewai, simple_flow, test_spans):
    simple_flow.kickoff(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_simple_flow_events(spans)
    _assert_simple_flow_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_simple_flow_async(crewai, simple_flow_async, test_spans):
    await simple_flow_async.kickoff_async(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_simple_flow_events(spans)
    _assert_simple_flow_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_complex_flow(crewai, complex_flow, test_spans):
    complex_flow.kickoff(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_complex_flow_events(spans)
    _assert_complex_flow_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_complex_flow_async(crewai, complex_flow_async, test_spans):
    await complex_flow_async.kickoff_async(inputs={"continent": "North America"})
    spans = _ordered_spans(test_spans)
    _assert_complex_flow_events(spans)
    _assert_complex_flow_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
def test_router_flow(crewai, router_flow, test_spans):
    router_flow.kickoff()
    spans = _ordered_spans(test_spans)
    _assert_router_flow_events(spans)
    _assert_router_flow_links(spans)


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
async def test_router_flow_async(crewai, router_flow_async, test_spans):
    await router_flow_async.kickoff_async()
    spans = _ordered_spans(test_spans)
    _assert_router_flow_events(spans)
    _assert_router_flow_links(spans)
