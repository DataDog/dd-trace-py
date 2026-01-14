#!/usr/bin/env python3
"""
Demo Part 3a: Warm Up Cache

Run this BEFORE stopping the registry service.
This simulates an application starting up and fetching prompts.
"""

import os

# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "customer-chatbot")

from ddtrace.llmobs import LLMObs

print("Application starting up... fetching prompts from registry")
prompt = LLMObs.get_prompt("greeting", label="prod")
print(f"Got prompt: {prompt.template[:50]}...")
print()
print("Application is now serving customers happily...")
print(prompt.format(name="Customer 1", company="Acme"))
