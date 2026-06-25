"""
Deterministic, dict-output example that mirrors the stock app's PortfolioBriefing
shape — so we can demo the comparators without paid LLM calls.

Output: {generated_at, analyses: [{ticker, sentiment}], summary}

Env knobs simulate the drift you'd see between a capture and a later replay:
    RUN_TS=<str>   value of generated_at (differs per run, like a timestamp)
    DRIFT=text     same structure, different summary wording (LLM non-determinism)
    DRIFT=drop     drop the last ticker (a real structural regression)
"""

import asyncio
import os

from experiment_poc import experiment_start


@experiment_start(name="briefing", inputs=["tickers"], output=lambda ret: ret)
async def analyze(tickers):
    await asyncio.sleep(0)
    drift = os.environ.get("DRIFT", "")

    analyses = [{"ticker": t, "sentiment": "bullish" if t == "NVDA" else "neutral"} for t in tickers]
    if drift == "drop" and len(analyses) > 1:
        analyses = analyses[:-1]  # a regression: a ticker silently dropped

    summary = (
        "AI demand keeps semis strong; NVDA out front."
        if drift == "text"
        else "Tech momentum led by NVDA."
    )

    return {
        "generated_at": os.environ.get("RUN_TS", "T0"),
        "analyses": analyses,
        "summary": summary,
    }


async def generate_traffic():
    await analyze(["NVDA", "AMD"])
