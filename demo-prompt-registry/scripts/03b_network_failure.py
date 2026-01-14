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
os.environ.setdefault("DD_LLMOBS_ML_APP", "customer-chatbot")

from ddtrace.llmobs import LLMObs


print("Simulating network failure - endpoint is UNREACHABLE")
print("(pointing to https://localhost:9999)")
print()

# These calls work because prompts are cached!
prompt = LLMObs.get_prompt("greeting", label="prod")
print(f"Source: {prompt.source}")
print()
print("Customer 2 arrives:")
print(prompt.format(name="Customer 2", company="Acme"))
print()
print("Customer 3 arrives:")
print(prompt.format(name="Customer 3", company="Acme"))
print()
print("Your application never skipped a beat - served from cache!")
