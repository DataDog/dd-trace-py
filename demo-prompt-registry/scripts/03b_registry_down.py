#!/usr/bin/env python3
"""
Demo Part 3b: Registry Down - Cache Saves the Day

Run this AFTER stopping the registry service (Ctrl+C in Terminal 1).
Shows that the application keeps working from cached prompts.
"""

import os


os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "http://localhost:8080"

from ddtrace.llmobs._prompts.manager import PromptManager


# Same manager, same app - but now the registry is DOWN
manager = PromptManager(api_key="my-api-key", site="datadoghq.com", ml_app="customer-chatbot")

print("Registry is down, but customers keep coming...")
print()

# These calls work because prompts are cached!
prompt = manager.get_prompt("greeting", label="prod")
print(f"Source: {prompt.source}")
print()
print("Customer 2 arrives:")
print(prompt.format(name="Customer 2", company="Acme"))
print()
print("Customer 3 arrives:")
print(prompt.format(name="Customer 3", company="Acme"))
print()
print("Your application never skipped a beat.")
