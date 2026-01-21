#!/usr/bin/env python3
"""
Demo Part 3b: Registry Down - Cache Saves the Day

Run this AFTER running 03a_warmup_cache.py to populate the cache.
Shows that the application keeps working from cached prompts.
"""

import os


# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "session-summary-eval")

from ddtrace.llmobs import LLMObs


print("Testing cache resilience...")
print()

# These calls work because prompts are cached!
prompt = LLMObs.get_prompt("summary", label="prod")
print(f"Source: {prompt.source}")
print()
print("Request 2 arrives:")
rendered = prompt.format(event_context="view,/settings,0\naction,update-profile,2000")
print(f"Rendered {len(rendered)} messages")
print()
print("Request 3 arrives:")
rendered = prompt.format(event_context="view,/orders,0\naction,view-order,1500")
print(f"Rendered {len(rendered)} messages")
print()
print("Your application never skipped a beat.")
