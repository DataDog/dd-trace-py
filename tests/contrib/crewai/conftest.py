import os
import time
from unittest import mock

from crewai import LLM
from crewai import Agent
from crewai import Crew
from crewai import Flow
from crewai import Process
from crewai import Task
from crewai.flow.flow import and_
from crewai.flow.flow import listen
from crewai.flow.flow import router
from crewai.flow.flow import start
from crewai.tasks.conditional_task import ConditionalTask
from crewai.tools import tool
import pytest
import vcr

from ddtrace.contrib.internal.crewai.patch import patch
from ddtrace.contrib.internal.crewai.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.crewai.utils import CREWAI_VERSION
from tests.contrib.crewai.utils import budget_text
from tests.contrib.crewai.utils import fun_fact_text
from tests.contrib.crewai.utils import itinerary_text
from tests.contrib.crewai.utils import welcome_email_text
from tests.utils import override_global_config


CASSETTE_DIR = os.path.join(os.path.dirname(__file__), "cassettes")


def _cassette_path(path):
    # crewai 1.x makes native function calls and structured planner requests, so interactions whose requests changed
    # were recorded again under cassettes/v1; crewai<1.0 keeps replaying the original recordings.
    v1_path = os.path.join(CASSETTE_DIR, "v1", os.path.basename(path))
    return v1_path if CREWAI_VERSION >= (1, 0, 0) and os.path.exists(v1_path) else path


@pytest.fixture
def request_vcr():
    yield vcr.VCR(
        cassette_library_dir=CASSETTE_DIR,
        path_transformer=_cassette_path,
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent and crewai telemetry endpoint
        ignore_localhost=True,
        ignore_hosts=["testagent", "telemetry.crewai.com"],
    )


# OpenAI's strict function schemas, used by crewai 1.x native tool calls, reject an untyped list argument.
@tool("Average Calculator")
def calculate_average_tool(entries: list[float]) -> float:
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
        manager_llm="gpt-4o-mini",  # the model the crewai 1.x hierarchical recording was made with
        planning=True,
    )


@pytest.fixture
def failing_llm_crew(crewai):
    # OpenAI rejects temperature=5, so the recorded LLM call fails; max_retry_limit=0 stops the agent from retrying.
    agent = Agent(
        role="Senior Research Scientist",
        goal="Summarize developments in AI agents",
        backstory="You're a seasoned researcher.",
        llm=LLM(model="gpt-4.1-mini", temperature=5),
        max_retry_limit=0,
    )
    task = Task(description="Summarize AI agents in one sentence.", expected_output="One sentence.", agent=agent)
    yield Crew(agents=[agent], tasks=[task], process=Process.sequential)


@pytest.fixture
def openai_traced_tool_crew(crewai):
    """tool_crew with a fresh agent, so its LLM creates its OpenAI client after openai is patched."""
    from ddtrace.contrib.internal.openai.patch import patch as patch_openai
    from ddtrace.contrib.internal.openai.patch import unpatch as unpatch_openai

    patch_openai()
    agent = Agent(
        role=analytics_agent.role,
        goal=analytics_agent.goal,
        backstory=analytics_agent.backstory,
        tools=[calculate_average_tool],
    )
    task = Task(
        name=data_analysis_task_1.name,
        description=data_analysis_task_1.description,
        expected_output=data_analysis_task_1.expected_output,
        agent=agent,
        async_execution=True,
    )
    yield Crew(agents=[agent], tasks=[task], process=Process.sequential)
    unpatch_openai()


@pytest.fixture
def streaming_crew(crewai):
    # Its own agent, because a streaming kickoff turns on streaming for the LLM of every agent in the crew.
    agent = Agent(
        role="Senior Research Scientist",
        goal="Summarize developments in AI agents",
        backstory="You're a seasoned researcher.",
        llm=LLM(model="gpt-4.1-mini"),
    )
    task = Task(description="Summarize AI agents in one sentence.", expected_output="One sentence.", agent=agent)
    yield Crew(agents=[agent], tasks=[task], process=Process.sequential, stream=True)


@pytest.fixture
def simple_flow(crewai):
    class ExFlow(Flow[dict]):
        model = "gpt-4o-mini"

        @start()
        def generate_city(self):
            time.sleep(0.05)
            return "New York City"

        @listen(generate_city)
        def generate_fun_fact(self, random_city):
            time.sleep(0.06)
            return fun_fact_text

    yield ExFlow()


@pytest.fixture
def simple_flow_async(crewai):
    class ExFlow(Flow[dict]):
        model = "gpt-4o-mini"

        @start()
        async def generate_city(self):
            time.sleep(0.05)
            return "New York City"

        @listen(generate_city)
        async def generate_fun_fact(self, random_city):
            time.sleep(0.06)
            return fun_fact_text

    yield ExFlow()


@pytest.fixture
def complex_flow(crewai):
    class ExFlow(Flow[dict]):
        model = "gpt-4o-mini"

        @start()
        def generate_city(self):
            time.sleep(0.05)
            return "New York City"

        @start()
        def generate_welcome_email(self):
            time.sleep(0.05)
            return welcome_email_text

        @listen(generate_city)
        def generate_fun_fact(self, random_city):
            time.sleep(0.06)
            return fun_fact_text

        @listen(generate_city)
        def generate_budget(self, random_city):
            time.sleep(0.04)
            return budget_text

        @listen(and_(generate_budget, generate_city, generate_fun_fact, generate_welcome_email))
        def generate_itinerary(self):
            time.sleep(0.05)
            return itinerary_text

    yield ExFlow()


@pytest.fixture
def complex_flow_async(crewai):
    class ExFlow(Flow[dict]):
        model = "gpt-4o-mini"

        @start()
        async def generate_city(self):
            time.sleep(0.05)
            return "New York City"

        @start()
        async def generate_welcome_email(self):
            time.sleep(0.05)
            return welcome_email_text

        @listen(generate_city)
        async def generate_fun_fact(self, random_city):
            time.sleep(0.06)
            return fun_fact_text

        @listen(generate_city)
        async def generate_budget(self, random_city):
            time.sleep(0.04)
            return budget_text

        @listen(and_(generate_budget, generate_city, generate_fun_fact, generate_welcome_email))
        async def generate_itinerary(self):
            time.sleep(0.05)
            return itinerary_text

    yield ExFlow()


@pytest.fixture
def router_flow(crewai):
    class ExFlow(Flow[dict]):
        model = "gpt-4o-mini"

        @start()
        def generate_city(self):
            time.sleep(0.05)
            random_city = "New York City"
            self.state["city"] = random_city
            return random_city

        @router(generate_city)
        def discriminate_city(self):
            time.sleep(0.05)
            if self.state["city"] != "New York City":
                return "YIKES"
            return "LFG"

        @listen("YIKES")
        def say_oop(self):
            time.sleep(0.03)
            return "Oop, have a fun trip!"

        @listen("LFG")
        def generate_fun_fact(self):
            time.sleep(0.06)
            return fun_fact_text

    yield ExFlow()


@pytest.fixture
def router_flow_async(crewai):
    class ExFlow(Flow[dict]):
        model = "gpt-4o-mini"

        @start()
        async def generate_city(self):
            time.sleep(0.05)
            random_city = "New York City"
            self.state["city"] = random_city
            return random_city

        @router(generate_city)
        async def discriminate_city(self):
            time.sleep(0.05)
            if self.state["city"] != "New York City":
                return "YIKES"
            return "LFG"

        @listen("YIKES")
        async def say_oop(self):
            time.sleep(0.03)
            return "Oop, have a fun trip!"

        @listen("LFG")
        async def generate_fun_fact(self):
            time.sleep(0.06)
            return fun_fact_text

    yield ExFlow()


@pytest.fixture
def crewai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    # crewai telemetry exports from a background thread. VCR sends ignored-host requests with all of its patches
    # removed process-wide, so that export can let a concurrent LLM call escape the cassette.
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "true")
    patch()
    import crewai

    yield crewai
    unpatch()


@pytest.fixture
def crewai_llmobs(tracer, monkeypatch):
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-api_key>",
            "service": "tests.contrib.crewai",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()
