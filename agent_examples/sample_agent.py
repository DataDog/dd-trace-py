import asyncio
import os
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

import ddtrace.auto


# --- Sample tools ---
def search_docs(query: str) -> dict[str, Any]:
    """
    A tiny search tool stub. Replace with real search logic or API.
    """
    return {"results": [f"Found reference for: {query}"]}


def multiply(a: int, b: int) -> dict[str, Any]:
    """
    Simple arithmetic tool to showcase function tools.
    """
    return {"product": a * b}


async def main():
    # LLM configuration (uses google-genai under the hood). Ensure GOOGLE_API_KEY is set.
    model = Gemini(model="gemini-2.5-pro")

    # Wrap Python callables as tools the agent can invoke.
    tools = [
        FunctionTool(func=search_docs),
        FunctionTool(func=multiply),
    ]

    # Enable code execution so the model can emit code blocks that get executed.
    code_executor = UnsafeLocalCodeExecutor()

    agent = LlmAgent(
        name="sample_builder",
        description="Helps prototype tiny apps by planning, searching, coding.",
        model=model,
        tools=tools,  # type: ignore[arg-type]
        code_executor=code_executor,
        # A light instruction. The model can plan, call tools, then write code.
        instruction=(
            "You are a helpful builder. When needed: (1) plan, (2) search, (3) write"
            " short Python to compute or transform results. Prefer concise outputs."
        ),
    )

    runner = InMemoryRunner(agent=agent, app_name="SampleADKApp")

    # Ensure the session exists before running
    await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id="user-123",
        session_id="session-001",
    )

    # Compose a request that typically triggers tool use and code execution.
    new_message = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "You must call a tool before executing any code."
                    " First, CALL the `search_docs` tool with the query 'recurring revenue tips'"
                    " and include the results briefly. Next, CALL the `multiply` tool with a=37 and b=29"
                    " to compute monthly revenue. Only after both tool calls, EXECUTE code"
                    " to compute 12-month total revenue by emitting a fenced Python block in the exact format:\n\n"
                    "```python\n"
                    "# compute monthly and yearly revenue\n"
                    "units = 37\n"
                    "price = 29\n"
                    "monthly = units * price\n"
                    "yearly = monthly * 12\n"
                    "print({'monthly': monthly, 'yearly': yearly})\n"
                    "```\n\n"
                    "Only include one code block as shown so the code executor runs it."
                )
            )
        ],
    )

    print("\n--- Running agent ---\n")
    async for event in runner.run_async(
        user_id="user-123",
        session_id="session-001",
        new_message=new_message,
    ):
        # Stream the events; final responses will have event.is_final_response()
        author = event.author or ""
        text = None
        if event.content and event.content.parts:
            # Concatenate visible text parts for demo output
            text = "".join([p.text or "" for p in event.content.parts if p.text])
        if text:
            print(f"[{author}] {text}")


if __name__ == "__main__":
    # Ensure API key presence for quick feedback
    if not os.environ.get("GOOGLE_API_KEY"):
        print("Warning: GOOGLE_API_KEY not set. Set it to run against Google GenAI.")
    asyncio.run(main())
