"""Measure Agents import/patch cost and the canonical raw receive boundary."""

import asyncio
import os
import subprocess

import bm


class OpenAIAgentsRealtime(bm.Scenario):
    mode: str
    cprofile_loops: int = 0

    def run(self):
        if self.mode == "startup":
            environment = dict(os.environ, DD_LLMOBS_ENABLED="false")

            def run_startup(loops):
                for _ in range(loops):
                    subprocess.check_call(
                        ["python", "-c", "import ddtrace.auto; import agents.realtime.openai_realtime"],
                        env=environment,
                    )

            yield run_startup
            return

        import agents
        from agents.realtime import openai_realtime

        from ddtrace import patch
        from ddtrace.llmobs import LLMObs

        agents.set_trace_processors([])
        LLMObs.enable(ml_app="realtime-benchmark", agentless_enabled=False, integrations_enabled=False)
        patch(openai_agents=True)
        original = openai_realtime.websockets.connect

        class Socket:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Future()

            async def send(self, payload):
                pass

            async def close(self):
                pass

        async def connect(*args, **kwargs):
            return Socket()

        openai_realtime.websockets.connect = connect
        loop = asyncio.new_event_loop()
        model = openai_realtime.OpenAIRealtimeWebSocketModel()
        loop.run_until_complete(model.connect({"api_key": "benchmark", "initial_model_settings": {"tracing": None}}))
        event = {"type": "rate_limits.updated", "event_id": "benchmark-event", "rate_limits": []}

        async def receive(loops):
            for _ in range(loops):
                await model._handle_ws_event(event)

        def run_receive(loops):
            loop.run_until_complete(receive(loops))

        try:
            yield run_receive
        finally:
            loop.run_until_complete(model.close())
            loop.close()
            openai_realtime.websockets.connect = original
            LLMObs.disable()
