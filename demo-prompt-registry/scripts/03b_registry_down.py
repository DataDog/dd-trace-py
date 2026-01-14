#!/usr/bin/env python3
"""
Demo Part 3b: Registry Down - Cache Saves the Day

Run this AFTER stopping the registry service (Ctrl+C in Terminal 1).
Shows that the application keeps working from cached prompts.
"""

import os

# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "customer-chatbot")

from ddtrace.llmobs import LLMObs

print("Registry is down, but customers keep coming...")
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
print("Your application never skipped a beat.")
