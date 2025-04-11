import os

from crewai import Agent
from crewai import Crew
from crewai import Process
from crewai import Task
from crewai.tasks.conditional_task import ConditionalTask
from crewai.tools import tool
import pytest
import vcr

from ddtrace.contrib.internal.crewai.patch import patch
from ddtrace.contrib.internal.crewai.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def request_vcr():
    yield vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@tool("Average Calculator")
def calculate_average_tool(entries: list) -> float:
    """This tool returns the average of a list of numbers."""
    return sum(entries) / len(entries)


researcher_agent = Agent(
    role="Senior Research Scientist",
    goal="Uncover cutting-edge developments in {topic}",
    backstory="You're a seasoned researcher with a knack for uncovering the latest developments in {topic}. "
    "Known for your ability to find the most relevant information and present it in a clear "
    "and concise manner.",
)

reporter_agent = Agent(
    role="{topic} Reporting Analyst",
    goal="Create detailed reports based on {topic} data analysis and research findings",
    backstory="You're a meticulous analyst with a keen eye for detail. You're known for your ability to turn complex "
    "data into clear and concise reports, making it easy for others to understand and act on the "
    "information you provide.",
)

analytics_agent = Agent(
    role="Python Data Analyst",
    goal="Analyze data and provide insights using Python",
    backstory="You are an experienced data analyst with strong Python skills.",
    tools=[calculate_average_tool],
)

recommender_agent = Agent(
    role="Tour Guide",
    goal="Recommend fun activities for a group of humans.",
    backstory="You are a tour guide with a passion for finding the best activities for groups of people.",
)

research_task = Task(
    description="Conduct a thorough research about {topic}. Make sure you find any interesting and relevant "
    "information given the current year is 2025.",
    expected_output="A list with 3 bullet points of the most relevant information about {topic}",
    agent=researcher_agent,
)

reporting_task = Task(
    description="Review the context you got and expand each topic into a full section for a report. "
    "Make sure the report is detailed and contains any and all relevant information.",
    expected_output="A fully fledged report with the main topics, each with a full section of information.",
    agent=reporter_agent,
)

data_analysis_task_1 = Task(
    name="Calculate participants' average ages",
    description="Analyze the given dataset and calculate the average age of participants. Ages: {ages}",
    expected_output="A complete sentence showing the question and the answer.",
    agent=analytics_agent,
    async_execution=True,
)

recommend_task = Task(
    name="Recommend fun activities",
    description="Given the average age of participants, recommend a fun activity for the group.",
    expected_output="Blog post with 3 bullet points of recommended activities.",
    agent=recommender_agent,
    context=[data_analysis_task_1],
)

conditional_task = ConditionalTask(
    name="Conditional Task",
    description="This task will not be executed.",
    expected_output="A single sentence explaining why this task was executed somehow if it was indeed executed.",
    agent=recommender_agent,
    context=[data_analysis_task_1],
    condition=lambda x: False,
)


@pytest.fixture
def basic_crew(crewai):
    yield Crew(
        agents=[researcher_agent, reporter_agent], tasks=[research_task, reporting_task], process=Process.sequential
    )


@pytest.fixture
def tool_crew(crewai):
    yield Crew(agents=[analytics_agent], tasks=[data_analysis_task_1], process=Process.sequential)


@pytest.fixture
def async_exec_crew(crewai):
    yield Crew(
        agents=[analytics_agent, recommender_agent],
        tasks=[data_analysis_task_1, recommend_task],
        process=Process.sequential,
    )


@pytest.fixture
def conditional_crew(crewai):
    yield Crew(
        agents=[analytics_agent, recommender_agent],
        tasks=[data_analysis_task_1, conditional_task, recommend_task],
        process=Process.sequential,
    )


@pytest.fixture
def hierarchical_crew(crewai):
    data_analysis_task = Task(
        name="Calculate participants' average ages",
        description="Analyze the given dataset and calculate the average age of participants. Ages: {ages}",
        expected_output="A complete sentence showing the question and the answer.",
    )
    recommend_task = Task(
        name="Recommend fun activities",
        description="Given the average age of participants, recommend a fun activity for the group.",
        expected_output="Blog post with 3 bullet points of recommended activities.",
        context=[data_analysis_task],
    )
    yield Crew(
        agents=[analytics_agent, recommender_agent],
        tasks=[data_analysis_task, recommend_task],
        process=Process.hierarchical,
        manager_llm="gpt-4o",
        planning=True,
    )


@pytest.fixture
def crewai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    patch()
    import crewai

    yield crewai
    unpatch()


@pytest.fixture
def mock_tracer(crewai):
    pin = Pin.get_from(crewai)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(crewai, tracer=mock_tracer)
    yield mock_tracer


@pytest.fixture
def crewai_llmobs(mock_tracer, llmobs_span_writer):
    llmobs_service.disable()
    with override_global_config(
        {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.contrib.crewai"}
    ):
        llmobs_service.enable(_tracer=mock_tracer, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, "datad0g.com", "<not-a-real-key>", is_agentless=True)


@pytest.fixture
def llmobs_events(crewai_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events
