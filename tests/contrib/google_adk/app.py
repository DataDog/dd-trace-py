"""Test application setup for Google ADK integration tests."""
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools.function_tool import FunctionTool
from google.genai import types


def search_docs(query: str) -> dict[str, Any]:
    """A tiny search tool stub."""
    return {"results": [f"Found reference for: {query}"]}


def multiply(a: int, b: int) -> dict[str, Any]:
    """Simple arithmetic tool."""
    return {"product": a * b}


async def setup_test_agent():
    """Set up a test agent with tools and code executor."""
    model = Gemini(model="gemini-2.5-pro")

    # Wrap Python callables as tools the agent can invoke
    tools = [
        FunctionTool(func=search_docs),
        FunctionTool(func=multiply),
    ]

    # Enable code execution so the model can emit code blocks that get executed
    code_executor = UnsafeLocalCodeExecutor()

    agent = LlmAgent(
        name="test_agent",
        description="Test agent for ADK integration testing",
        model=model,
        tools=tools,  # type: ignore[arg-type]
        code_executor=code_executor,
        instruction=(
            "You are a helpful test agent. You can: (1) call tools using the provided functions, "
            "(2) execute Python code blocks when they are provided to you. "
            "When you see ```python code blocks, execute them using your code execution capability. "
            "Always be helpful and use your available capabilities."
        ),
    )

    runner = InMemoryRunner(agent=agent, app_name="TestADKApp")
    await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id="test-user",
        session_id="test-session",
    )

    return runner


def create_test_message(text: str) -> types.Content:
    """Create a test message content."""
    return types.Content(
        role="user",
        parts=[types.Part(text=text)],
    )
