#!/usr/bin/env python3
"""
Demo Part 1: Basic Prompt Fetch

Shows fetching a text template prompt and rendering it with variables.
"""

import os


# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "customer-chatbot")

from ddtrace.llmobs import LLMObs


prompt = LLMObs.get_prompt("greeting", label="prod")

print("Fetched prompt from registry!")
print(f"Template: {prompt.template}")
print()
print("Now rendering for a real customer...")
print(prompt.format(name="Sarah", company="Acme Inc"))
