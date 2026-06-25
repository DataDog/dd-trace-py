"""
Mirrors lux's REAL boundary shape so we can validate the mechanics without
lux's infra:

    @llmobs_agent(name="lux_agent")
    async def _run_lux_agent(agent, deps, message, user_handle=""):
        return await agent.handle_message(deps, message)   # -> (response, deps)

Here `infra` stands in for the non-serializable agent/deps. We capture only
`message`/`user_handle` and extract the response from the returned tuple.

`PROMPT_SUFFIX` simulates a local agent code change.
"""

import asyncio
import os

from experiment_poc import experiment_start


PROMPT_SUFFIX = os.environ.get("PROMPT_SUFFIX", "")


class FakeInfra:
    """Stands in for the live agent + deps (MCP, Postgres, etc.) — NOT serializable."""

    def __init__(self):
        self.calls = 0


async def _build_fixtures() -> dict:
    """Rebuild the live infra at replay time. In real lux this is:
        refresher = JwtRefresher(...); refresher.start()
        runtime = await create_lux_runtime(org_id=..., jwt_getter=refresher.get_token)
        deps = LuxAgentDependencies(... openai_provider, services, time_budget ...)
        return {"agent": runtime.agent, "deps": deps}
    """
    await asyncio.sleep(0)
    return {"infra": FakeInfra(), "deps": {"conversation_id": "c-replay"}}


@experiment_start(
    name="lux_agent",
    inputs=["message", "user_handle"],
    output=lambda ret: ret[0],
    fixtures=_build_fixtures,
)
async def run_agent(infra: "FakeInfra", deps: dict, message: str, user_handle: str = ""):
    """Single-function unit: message in, (response, deps) out — exactly lux's shape."""
    infra.calls += 1
    await asyncio.sleep(0)  # simulate I/O
    response = await _model(message)
    return (response, deps)  # like `agent_response, deps = await _run_lux_agent(...)`


async def _model(message: str) -> str:
    await asyncio.sleep(0)
    base = {
        "why is my app slow": "N+1 queries in the retrieval span",
        "explain trace abc123": "It errored at the tool-call span",
    }.get(message.strip().lower(), "I could not determine the cause")
    return base + PROMPT_SUFFIX


async def generate_traffic():
    infra = FakeInfra()
    deps = {"conversation_id": "c-1"}
    for msg in ["Why is my app slow", "Explain trace abc123", "What changed yesterday"]:
        await run_agent(infra, deps, msg, user_handle="mehul")
