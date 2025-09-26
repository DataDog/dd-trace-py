from __future__ import annotations

import os
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel
import torch
import torch.nn.functional as F
import vllm

# Optional: use vLLM's OpenAI server engine builder to exercise MQ client
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.entrypoints.openai.api_server import build_async_engine_client_from_engine_args
from vllm.usage.usage_lib import UsageContext

from ddtrace._trace.pin import Pin

from ._utils import create_async_engine


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
                max_model_len=256,
                compilation_config=0,
                trust_remote_code=True,
                disable_log_stats=True,
                gpu_memory_utilization=float(os.environ.get("VLLM_GPU_UTIL", "0.2")),
            )
            async with build_async_engine_client_from_engine_args(
                embed_args,
                usage_context=UsageContext.OPENAI_API_SERVER,
            ) as embed_engine:
                pooling_params = vllm.PoolingParams(task="encode")
                doc_vecs: List[torch.Tensor] = []
                for i, doc in enumerate(req.documents):
                    last = None
                    async for out in embed_engine.encode(
                        prompt=doc,
                        pooling_params=pooling_params,
                        request_id=f"embed-{i}",
                    ):
                        last = out
                        if out.finished:
                            break
                    if last and last.outputs is not None and hasattr(last.outputs, "data"):
                        emb = last.outputs.data
                        if emb.dim() > 1:
                            emb = emb.mean(dim=0)
                        emb = emb.detach().to("cpu", copy=True).float()
                        doc_vecs.append(emb)

                # Embed query
                q_last = None
                async for out in embed_engine.encode(
                    prompt=req.query,
                    pooling_params=pooling_params,
                    request_id="embed-query",
                ):
                    q_last = out
                    if out.finished:
                        break
                query_vec = None
                if q_last and q_last.outputs is not None and hasattr(q_last.outputs, "data"):
                    q = q_last.outputs.data
                    if q.dim() > 1:
                        q = q.mean(dim=0)
                    query_vec = q.detach().to("cpu", copy=True).float()

                # Select top document
                top_doc = req.documents[0]
                if query_vec is not None and doc_vecs:
                    sims = [F.cosine_similarity(query_vec.unsqueeze(0), d.unsqueeze(0)).item() for d in doc_vecs]
                    top_idx = int(max(range(len(sims)), key=lambda i: sims[i]))
                    top_doc = req.documents[top_idx]
                # Free allocator cache before next engine
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

            # 2) Generate
            gen_args = AsyncEngineArgs(
                model="facebook/opt-125m",
                enforce_eager=True,
                max_model_len=256,
                compilation_config=0,
                trust_remote_code=True,
                disable_log_stats=True,
                gpu_memory_utilization=float(os.environ.get("VLLM_GPU_UTIL", "0.2")),
            )
            async with build_async_engine_client_from_engine_args(
                gen_args,
                usage_context=UsageContext.OPENAI_API_SERVER,
            ) as gen_engine:
                sampling = vllm.SamplingParams(temperature=0.8, top_p=0.95, max_tokens=64, seed=42)
                prompt = f"Context: {top_doc}\nQuestion: {req.query}\nAnswer:"
                generated_text = ""
                last = None
                async for out in gen_engine.generate(
                    prompt=prompt,
                    sampling_params=sampling,
                    request_id="gen-0",
                ):
                    last = out
                    if out.finished:
                        break
                if last and last.outputs:
                    sample = last.outputs[0] if isinstance(last.outputs, list) and last.outputs else None
                    if sample and hasattr(sample, "text") and sample.text:
                        generated_text = sample.text
                return {"generated_text": generated_text, "retrieved_document": top_doc}
        else:
            # Exercise in-process async engines (V1 AsyncLLM or V0 AsyncLLMEngine)
            # 1) Embed candidate documents
            embed_engine = create_async_engine(
                model="intfloat/e5-small-v2",
                engine_mode=engine_mode,
                enforce_eager=True,
                runner="pooling",
                max_model_len=256,
                compilation_config=0,
                trust_remote_code=True,
                gpu_memory_utilization=float(os.environ.get("VLLM_GPU_UTIL", "0.2")),
            )
            pooling_params = vllm.PoolingParams(task="encode")
            doc_vecs: List[torch.Tensor] = []
            for i, doc in enumerate(req.documents):
                last = None
                async for out in embed_engine.encode(
                    prompt=doc,
                    pooling_params=pooling_params,
                    request_id=f"embed-{i}",
                ):
                    last = out
                    if out.finished:
                        break
                if last and last.outputs is not None and hasattr(last.outputs, "data"):
                    emb = last.outputs.data
                    if emb.dim() > 1:
                        emb = emb.mean(dim=0)
                    emb = emb.detach().to("cpu", copy=True).float()
                    doc_vecs.append(emb)

            # Embed query
            q_last = None
            async for out in embed_engine.encode(
                prompt=req.query,
                pooling_params=pooling_params,
                request_id="embed-query",
            ):
                q_last = out
                if out.finished:
                    break
            query_vec = None
            if q_last and q_last.outputs is not None and hasattr(q_last.outputs, "data"):
                q = q_last.outputs.data
                if q.dim() > 1:
                    q = q.mean(dim=0)
                query_vec = q.detach().to("cpu", copy=True).float()

            top_doc = req.documents[0]
            if query_vec is not None and doc_vecs:
                sims = [F.cosine_similarity(query_vec.unsqueeze(0), d.unsqueeze(0)).item() for d in doc_vecs]
                top_idx = int(max(range(len(sims)), key=lambda i: sims[i]))
                top_doc = req.documents[top_idx]
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

            # 2) Generate an answer using the query and the first document as context
            gen_engine = create_async_engine(
                model="facebook/opt-125m",
                engine_mode=engine_mode,
                enforce_eager=True,
                max_model_len=256,
                compilation_config=0,
                trust_remote_code=True,
                gpu_memory_utilization=float(os.environ.get("VLLM_GPU_UTIL", "0.2")),
            )
            sampling = vllm.SamplingParams(temperature=0.8, top_p=0.95, max_tokens=64, seed=42)
            prompt = f"Context: {top_doc}\nQuestion: {req.query}\nAnswer:"
            generated_text = ""
            last = None
            async for out in gen_engine.generate(
                prompt=prompt,
                sampling_params=sampling,
                request_id="gen-0",
            ):
                last = out
                if out.finished:
                    break
            if last and last.outputs:
                sample = last.outputs[0] if isinstance(last.outputs, list) and last.outputs else None
                if sample and hasattr(sample, "text") and sample.text:
                    generated_text = sample.text

    return {"generated_text": generated_text, "retrieved_document": top_doc}
