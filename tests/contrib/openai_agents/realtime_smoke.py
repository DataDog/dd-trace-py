"""Live smoke harness: run with ddtrace-run and the normal LLMObs environment.

Supply an already accepted call ID to attach without changing webhooks or
accepting a call. Without a call ID, send a text prompt over a new WebSocket.
"""

import argparse
import asyncio

from agents import function_tool
from agents.realtime import RealtimeAgent
from agents.realtime import RealtimeRunner
from agents.realtime.openai_realtime import OpenAIRealtimeSIPModel


@function_tool
async def availability(party_size: int) -> str:
    """Return demonstration availability for a party; does not create a booking."""
    return f"Demo availability for {party_size} people: 18:00 or 19:30. No reservation has been made."


async def main(call_id: str, message: str) -> None:
    agent = RealtimeAgent(
        name="Availability assistant",
        instructions=(
            "Give concise answers. Use availability for availability questions. Never claim to create a booking."
        ),
        tools=[availability],
    )
    runner = RealtimeRunner(
        agent, model=OpenAIRealtimeSIPModel() if call_id else None, config={"tracing_disabled": True}
    )
    options = {"call_id": call_id} if call_id else {"initial_model_settings": {"output_modalities": ["text"]}}
    async with await runner.run(model_config=options) as session:
        if message or not call_id:
            await session.send_message(message or "What availability do you have for two people?")
        async for event in session:
            # Event kinds confirm application visibility without logging raw audio,
            # transcripts, or provider payloads from a live caller.
            print(event.type)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call-id", default="", help="An already accepted SIP or WebRTC call ID")
    parser.add_argument("--message", default="", help="Optional initial text message")
    options = parser.parse_args()
    try:
        asyncio.run(main(options.call_id, options.message))
    except KeyboardInterrupt:
        pass
