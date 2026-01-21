#!/usr/bin/env python3
"""
Demo Part 1: Basic Prompt Fetch

Shows fetching a text template prompt and rendering it with variables.
"""

import os


# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "session-summary-eval")

from ddtrace.llmobs import LLMObs

LLMObs.clear_prompt_cache()


prompt = LLMObs.get_prompt("summary", label="prod")

print("Fetched prompt from registry!")
print(f"Template: {prompt.template}")
print(f"Source: {prompt.source}")
print()
print("Now rendering...")
print(prompt.format())
