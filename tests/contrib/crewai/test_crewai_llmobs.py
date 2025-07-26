import mock

from tests.llmobs._utils import _assert_span_link
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


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


def expected_agent_span_args(role):
    return {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": {"agent_manifest": AGENT_TO_EXPECTED_AGENT_MANIFEST[role]},
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
        "span_links": True,
    }


def _assert_basic_crew_events(llmobs_events, spans):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) == 5
    expected_span_args = {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": mock.ANY,
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
        "span_links": True,
    }
    for llmobs_span, span, kind in zip(llmobs_events, spans, ("workflow", "task", "agent", "task", "agent")):
        extra_args = expected_agent_span_args(llmobs_span["name"]) if kind == "agent" else expected_span_args
        assert llmobs_span == _expected_llmobs_non_llm_span_event(span, span_kind=kind, **extra_args)


def _assert_basic_crew_links(llmobs_events):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    # span links for crew -> task
    _assert_span_link(llmobs_events[0], llmobs_events[1], "input", "input")
    _assert_span_link(llmobs_events[1], llmobs_events[3], "output", "input")
    _assert_span_link(llmobs_events[3], llmobs_events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(llmobs_events[1], llmobs_events[2], "input", "input")
    _assert_span_link(llmobs_events[2], llmobs_events[1], "output", "output")
    _assert_span_link(llmobs_events[3], llmobs_events[4], "input", "input")
    _assert_span_link(llmobs_events[4], llmobs_events[3], "output", "output")


def _assert_tool_crew_events(llmobs_events, spans):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) == 4
    expected_span_args = {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": mock.ANY,
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
        "span_links": True,
    }
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(spans[0], span_kind="workflow", **expected_span_args)
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(spans[1], span_kind="task", **expected_span_args)
    assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
        spans[2], span_kind="agent", **expected_agent_span_args(llmobs_events[2]["name"])
    )
    assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
        spans[3],
        span_kind="tool",
        input_value=mock.ANY,
        output_value=mock.ANY,
        metadata=mock.ANY,
        tags={"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
    )


def _assert_tool_crew_links(llmobs_events):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    # span links for crew -> task
    _assert_span_link(llmobs_events[0], llmobs_events[1], "input", "input")
    _assert_span_link(llmobs_events[1], llmobs_events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(llmobs_events[1], llmobs_events[2], "input", "input")
    _assert_span_link(llmobs_events[2], llmobs_events[1], "output", "output")


def _assert_async_crew_events(llmobs_events, spans):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) == 6
    expected_span_args = {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": mock.ANY,
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
        "span_links": True,
    }
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(spans[0], span_kind="workflow", **expected_span_args)
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(spans[1], span_kind="task", **expected_span_args)
    assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
        spans[2], span_kind="agent", **expected_agent_span_args(llmobs_events[2]["name"])
    )
    assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
        spans[3],
        span_kind="tool",
        input_value=mock.ANY,
        output_value=mock.ANY,
        metadata=mock.ANY,
        tags={"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
    )
    assert llmobs_events[4] == _expected_llmobs_non_llm_span_event(spans[4], span_kind="task", **expected_span_args)
    assert llmobs_events[5] == _expected_llmobs_non_llm_span_event(
        spans[5], span_kind="agent", **expected_agent_span_args(llmobs_events[5]["name"])
    )


def _assert_async_crew_links(llmobs_events):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    # span links for crew -> task

    _assert_span_link(llmobs_events[0], llmobs_events[1], "input", "input")
    _assert_span_link(llmobs_events[1], llmobs_events[4], "output", "input")
    _assert_span_link(llmobs_events[4], llmobs_events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(llmobs_events[1], llmobs_events[2], "input", "input")
    _assert_span_link(llmobs_events[2], llmobs_events[1], "output", "output")
    _assert_span_link(llmobs_events[4], llmobs_events[5], "input", "input")
    _assert_span_link(llmobs_events[5], llmobs_events[4], "output", "output")


def _assert_hierarchical_crew_events(llmobs_events, spans):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    assert len(spans) == len(llmobs_events) == 12
    expected_span_args = {
        "input_value": mock.ANY,
        "output_value": mock.ANY,
        "metadata": mock.ANY,
        "tags": {"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
        "span_links": True,
    }
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
    for llmobs_span, span, kind in zip(llmobs_events, spans, expected_span_kinds):
        if kind is None:  # Not expecting any span links for this tool span
            assert llmobs_span == _expected_llmobs_non_llm_span_event(
                span,
                span_kind="tool",
                input_value=mock.ANY,
                output_value=mock.ANY,
                metadata=mock.ANY,
                tags={"service": "tests.contrib.crewai", "ml_app": "<ml-app-name>"},
            )
            continue
        assert llmobs_span == _expected_llmobs_non_llm_span_event(span, span_kind=kind, **expected_span_args)


def _assert_hierarchical_crew_links(llmobs_events):
    llmobs_events.sort(key=lambda span: span["start_ns"])
    # span links for crew -> task
    _assert_span_link(llmobs_events[0], llmobs_events[1], "input", "input")
    _assert_span_link(llmobs_events[1], llmobs_events[3], "output", "input")
    _assert_span_link(llmobs_events[3], llmobs_events[8], "output", "input")
    _assert_span_link(llmobs_events[8], llmobs_events[0], "output", "output")

    # span links for task -> agent
    _assert_span_link(llmobs_events[1], llmobs_events[2], "input", "input")
    _assert_span_link(llmobs_events[2], llmobs_events[1], "output", "output")
    _assert_span_link(llmobs_events[3], llmobs_events[4], "input", "input")
    _assert_span_link(llmobs_events[4], llmobs_events[3], "output", "output")
    _assert_span_link(llmobs_events[5], llmobs_events[6], "input", "input")
    _assert_span_link(llmobs_events[6], llmobs_events[5], "output", "output")
    _assert_span_link(llmobs_events[8], llmobs_events[9], "input", "input")
    _assert_span_link(llmobs_events[9], llmobs_events[8], "output", "output")
    _assert_span_link(llmobs_events[10], llmobs_events[11], "input", "input")
    _assert_span_link(llmobs_events[11], llmobs_events[10], "output", "output")


def test_basic_crew(crewai, basic_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff(inputs={"topic": "AI"})
    spans = mock_tracer.pop_traces()[0]
    _assert_basic_crew_events(llmobs_events, spans)
    _assert_basic_crew_links(llmobs_events)


def test_basic_crew_for_each(crewai, basic_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff_for_each(inputs=[{"topic": "AI"}])
    spans = mock_tracer.pop_traces()[0]
    _assert_basic_crew_events(llmobs_events, spans)
    _assert_basic_crew_links(llmobs_events)


async def test_basic_crew_async(crewai, basic_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_async(inputs={"topic": "AI"})
    spans = mock_tracer.pop_traces()[0]
    _assert_basic_crew_events(llmobs_events, spans)
    _assert_basic_crew_links(llmobs_events)


async def test_basic_crew_async_for_each(crewai, basic_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_for_each_async(inputs=[{"topic": "AI"}])
    spans = mock_tracer.pop_traces()[0]
    _assert_basic_crew_events(llmobs_events, spans)
    _assert_basic_crew_links(llmobs_events)


def test_crew_with_tool(crewai, tool_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_tool_crew_events(llmobs_events, spans)
    _assert_tool_crew_links(llmobs_events)


def test_crew_with_tool_for_each(crewai, tool_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = mock_tracer.pop_traces()[0]
    _assert_tool_crew_events(llmobs_events, spans)
    _assert_tool_crew_links(llmobs_events)


async def test_crew_with_tool_async(crewai, tool_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_tool_crew_events(llmobs_events, spans)
    _assert_tool_crew_links(llmobs_events)


async def test_crew_with_tool_async_for_each(crewai, tool_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = mock_tracer.pop_traces()[0]
    _assert_tool_crew_events(llmobs_events, spans)
    _assert_tool_crew_links(llmobs_events)


def test_async_crew(crewai, async_exec_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_async_crew_events(llmobs_events, spans)
    _assert_async_crew_links(llmobs_events)


def test_async_crew_for_each(crewai, async_exec_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = mock_tracer.pop_traces()[0]
    _assert_async_crew_events(llmobs_events, spans)
    _assert_async_crew_links(llmobs_events)


async def test_async_crew_async(crewai, async_exec_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_async_crew_events(llmobs_events, spans)
    _assert_async_crew_links(llmobs_events)


async def test_async_crew_async_for_each(crewai, async_exec_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = mock_tracer.pop_traces()[0]
    _assert_async_crew_events(llmobs_events, spans)
    _assert_async_crew_links(llmobs_events)


def test_conditional_crew(crewai, conditional_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        conditional_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_async_crew_events(llmobs_events, spans)
    _assert_async_crew_links(llmobs_events)


async def test_conditional_crew_async(crewai, conditional_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await conditional_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_async_crew_events(llmobs_events, spans)
    _assert_async_crew_links(llmobs_events)


def test_hierarchical_crew(crewai, hierarchical_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_hierarchical_crew_events(llmobs_events, spans)
    _assert_hierarchical_crew_links(llmobs_events)


def test_hierarchical_crew_for_each(crewai, hierarchical_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = mock_tracer.pop_traces()[0]
    _assert_hierarchical_crew_events(llmobs_events, spans)
    _assert_hierarchical_crew_links(llmobs_events)


async def test_hierarchical_crew_async(crewai, hierarchical_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})
    spans = mock_tracer.pop_traces()[0]
    _assert_hierarchical_crew_events(llmobs_events, spans)
    _assert_hierarchical_crew_links(llmobs_events)


async def test_hierarchical_crew_async_for_each(crewai, hierarchical_crew, request_vcr, mock_tracer, llmobs_events):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])
    spans = mock_tracer.pop_traces()[0]
    _assert_hierarchical_crew_events(llmobs_events, spans)
    _assert_hierarchical_crew_links(llmobs_events)
