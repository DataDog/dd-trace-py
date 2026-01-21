#!/usr/bin/env python3
"""
Demo Part 3a: Warm Up Cache

Run this BEFORE simulating network failure.
This simulates an application starting up and fetching prompts.
"""

import os


# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "session-summary-eval")

from ddtrace.llmobs import LLMObs


print("Application starting up... fetching prompts from registry")
prompt = LLMObs.get_prompt("summary", label="prod")
print(f"Got prompt with {len(prompt.template)} messages")
print(f"Source: {prompt.source}")
print(f"Variables: {prompt.variables}")
print()
print("Application is now serving requests happily...")
rendered = prompt.format(event_context="view,/dashboard,0")
print(f"Rendered {len(rendered)} messages")
