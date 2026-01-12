#!/usr/bin/env python3
"""
Demo Part 3a: Warm Up Cache

Run this BEFORE stopping the registry service.
This simulates an application starting up and fetching prompts.
"""

import os


os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "http://localhost:8080"

from ddtrace.llmobs._prompts.manager import PromptManager


manager = PromptManager(api_key="my-api-key", site="datadoghq.com", ml_app="customer-chatbot")

print("Application starting up... fetching prompts from registry")
prompt = manager.get_prompt("greeting", label="prod")
print(f"Got prompt: {prompt.template[:50]}...")
print()
print("Application is now serving customers happily...")
print(prompt.format(name="Customer 1", company="Acme"))
