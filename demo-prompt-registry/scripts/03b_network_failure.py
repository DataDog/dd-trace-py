#!/usr/bin/env python3
"""
Demo Part 3b: Network Failure Simulation

Simulates network failure by pointing to an unreachable endpoint.
Shows that the application keeps working from cached prompts.

IMPORTANT: Run 03a_warmup_cache.py FIRST to populate the cache!
"""

import os


# Point to an UNREACHABLE endpoint - simulating Datadog being down
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "https://localhost:9999"
os.environ.setdefault("DD_LLMOBS_ML_APP", "session-summary-eval")

from ddtrace.llmobs import LLMObs


print("Simulating network failure - endpoint is UNREACHABLE")
print("(pointing to https://localhost:9999)")
print()

# These calls work because prompts are cached!
prompt = LLMObs.get_prompt("summary", label="prod")
print(f"Source: {prompt.source}")
print()
print("Request 2 arrives:")
rendered = prompt.format(event_context="view,/checkout,0\naction,click-pay,5000")
print(f"Rendered {len(rendered)} messages")
print()
print("Request 3 arrives:")
rendered = prompt.format(event_context="view,/profile,0\nerror,auth-failed,1000")
print(f"Rendered {len(rendered)} messages")
print()
print("Your application never skipped a beat - served from cache!")
