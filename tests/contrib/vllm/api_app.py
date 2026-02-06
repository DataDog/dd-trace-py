from __future__ import annotations


from fastapi import FastAPI
from fastapi import Request
from pydantic import BaseModel
import torch
import torch.nn.functional as F
import vllm

from ddtrace import tracer as ddtracer
from ddtrace.propagation.http import HTTPPropagator

from ._utils import create_async_engine


class RagRequest(BaseModel):
    query: str
    documents: list[str]


app = FastAPI()

EMBED_PARAMS = {
    "model": "intfloat/e5-small-v2",
    "enforce_eager": True,
    "max_model_len": 256,
    "trust_remote_code": True,
    "gpu_memory_utilization": 0.1,
    "runner": "pooling",
}

GEN_PARAMS = {
    "model": "facebook/opt-125m",
    "enforce_eager": True,
    "max_model_len": 256,
    "trust_remote_code": True,
    "gpu_memory_utilization": 0.1,
}


async def embed_texts(engine, texts: list[str], base_request_id: str) -> list[torch.Tensor]:
    """Embed a list of texts and return their vector representations."""
    pooling_params = vllm.PoolingParams(task="embed")
    vectors: list[torch.Tensor] = []

    for i, text in enumerate(texts):
        request_id = f"{base_request_id}-{i}" if len(texts) > 1 else base_request_id
        last = None
        async for out in engine.encode(
            prompt=text,
            pooling_params=pooling_params,
            request_id=request_id,
        ):
            last = out
            if out.finished:
                break

        if last and last.outputs is not None and hasattr(last.outputs, "data"):
            emb = last.outputs.data
            if emb.dim() > 1:
                emb = emb.mean(dim=0)
            vectors.append(emb.detach().to("cpu", copy=True).float())

    return vectors


async def generate_text(engine, prompt: str, sampling_params: vllm.SamplingParams, request_id: str) -> str:
    """Generate text using the given prompt and sampling parameters."""
    last = None
    async for out in engine.generate(
        prompt=prompt,
        sampling_params=sampling_params,
        request_id=request_id,
    ):
        last = out
        if out.finished:
            break

    if last and last.outputs:
        sample = last.outputs[0] if isinstance(last.outputs, list) and last.outputs else None
        if sample and hasattr(sample, "text") and sample.text:
            return sample.text
    return ""


@app.post("/rag")
async def rag(req: RagRequest, request: Request):
    """RAG endpoint using vLLM V1 for embedding and text generation."""
    # Activate trace context from client headers if provided
    headers = dict(request.headers)
    ctx = HTTPPropagator.extract(headers)
    if ctx:
        ddtracer.context_provider.activate(ctx)

    # Create V1 embedding engine
    embed_engine = create_async_engine(**EMBED_PARAMS)
    doc_vecs = await embed_texts(embed_engine, req.documents, "embed")
    query_vecs = await embed_texts(embed_engine, [req.query], "embed-query")
    query_vec = query_vecs[0] if query_vecs else None

    # Find most similar document
    top_doc = req.documents[0]
    if query_vec is not None and doc_vecs:
        sims = [F.cosine_similarity(query_vec.unsqueeze(0), d.unsqueeze(0)).item() for d in doc_vecs]
        top_idx = int(max(range(len(sims)), key=lambda i: sims[i]))
        top_doc = req.documents[top_idx]

    torch.cuda.empty_cache()

    # Create V1 generation engine
    gen_engine = create_async_engine(**GEN_PARAMS)
    sampling = vllm.SamplingParams(temperature=0.8, top_p=0.95, max_tokens=64, seed=42)
    prompt = f"Context: {top_doc}\nQuestion: {req.query}\nAnswer:"
    generated_text = await generate_text(gen_engine, prompt, sampling, "gen-0")

    return {"generated_text": generated_text, "retrieved_document": top_doc}
