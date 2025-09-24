from __future__ import annotations

import os
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel

import vllm
from ddtrace._trace.pin import Pin

from ._utils import get_cached_async_engine, get_simple_chat_template

# Optional: use vLLM's OpenAI server engine builder to exercise MQ client
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.entrypoints.openai.api_server import (
    build_async_engine_client_from_engine_args,
)
from vllm.usage.usage_lib import UsageContext


class RagRequest(BaseModel):
    query: str
    documents: List[str]


app = FastAPI()


@app.post("/rag")
async def rag(req: RagRequest):
    engine_mode = os.environ.get("VLLM_USE_V1", "0")
    use_mq = os.environ.get("VLLM_USE_MQ", "0") == "1"

    # Use the same tracer as vLLM integration to ensure parent/child relation
    tracer = Pin.get_from(vllm).tracer
    with tracer.trace("api.rag", service="tests.contrib.vllm"):
        if use_mq and engine_mode == "0":
            # Exercise MQLLMEngineClient path via vLLM's builder (v0 MQ branch)
            # 1) Embed
            embed_args = AsyncEngineArgs(
                model="intfloat/e5-small-v2",
                enforce_eager=True,
                disable_log_stats=True,
                trust_remote_code=True,
            )
            async with build_async_engine_client_from_engine_args(
                embed_args,
                usage_context=UsageContext.OPENAI_API_SERVER,
            ) as embed_engine:
                pooling_params = vllm.PoolingParams(task="encode")
                for i, doc in enumerate(req.documents):
                    # Stream just to exercise the async path; break after first
                    async for _ in embed_engine.encode(
                        prompt=doc,
                        pooling_params=pooling_params,
                        request_id=f"embed-{i}",
                    ):
                        break

            # 2) Generate
            gen_args = AsyncEngineArgs(
                model="facebook/opt-125m",
                enforce_eager=True,
                disable_log_stats=True,
            )
            async with build_async_engine_client_from_engine_args(
                gen_args,
                usage_context=UsageContext.OPENAI_API_SERVER,
            ) as gen_engine:
                sampling = vllm.SamplingParams(
                    temperature=0.8, top_p=0.95, max_tokens=64, seed=42
                )
                prompt = f"Context: {req.documents[0]}\nQuestion: {req.query}\nAnswer:"
                print("Engine class: ", gen_engine.__class__)
                async for _ in gen_engine.generate(
                    prompt=prompt,
                    sampling_params=sampling,
                    request_id="gen-0",
                ):
                    break
        else:
            # Exercise in-process async engines (V1 AsyncLLM or V0 AsyncLLMEngine)
            # 1) Embed candidate documents
            embed_engine = get_cached_async_engine(
                model="intfloat/e5-small-v2",
                engine_mode=engine_mode,
                enforce_eager=True,
                runner="pooling",
            )
            pooling_params = vllm.PoolingParams(task="encode")
            for i, doc in enumerate(req.documents):
                async for _ in embed_engine.encode(
                    prompt=doc,
                    pooling_params=pooling_params,
                    request_id=f"embed-{i}",
                ):
                    break

            # 2) Generate an answer using the query and the first document as context
            gen_engine = get_cached_async_engine(
                model="facebook/opt-125m",
                engine_mode=engine_mode,
                enforce_eager=True,
            )
            sampling = vllm.SamplingParams(temperature=0.8, top_p=0.95, max_tokens=64, seed=42)
            prompt = f"Context: {req.documents[0]}\nQuestion: {req.query}\nAnswer:"
            print("[1] Engine class: ", gen_engine.__class__)
            async for _ in gen_engine.generate(
                prompt=prompt,
                sampling_params=sampling,
                request_id="gen-0",
            ):
                break

    return {"status": "ok"}


