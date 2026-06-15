"""
A stand-in for an instrumented LLM app.

The thing under test is the flow from `ingest` (where input ENTERS) to `emit`
(where output EXITS). They are deliberately *different functions* connected by
ordinary orchestration — one call tree — which is the case the start/end
decorators are designed for.

`PROMPT_SUFFIX` simulates a local code change: set it, replay, and watch the
outputs diverge from the captured baseline.
"""

import os

from experiment_poc import experiment_end
from experiment_poc import experiment_start


# Flip this (via env) to simulate "I changed my prompt/model locally".
PROMPT_SUFFIX = os.environ.get("PROMPT_SUFFIX", "")


@experiment_start
def ingest(query: str):
    """Input enters here."""
    cleaned = query.strip().lower()
    return orchestrate(cleaned)


def orchestrate(q: str):
    """Imagine retrieval + LLM calls etc."""
    answer = fake_model(q)
    return emit(answer)


def fake_model(q: str) -> str:
    """Deterministic stand-in for an LLM call."""
    base = {
        "capital of france": "Paris",
        "2+2": "4",
        "meaning of life": "42",
    }.get(q, "I don't know")
    return base + PROMPT_SUFFIX


@experiment_end
def emit(result: str):
    """Output exits here. In prod this would publish to the user / a queue —
    a real side effect we do NOT want to fire during replay."""
    print(f"  [emit side-effect] {result}")
    return result


def generate_traffic():
    """Drives a few inputs so `capture` has something to record."""
    for q in ["Capital of France", "2+2", "Meaning of life"]:
        ingest(q)
