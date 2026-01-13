#!/usr/bin/env python3
"""
Demo Part 3a: Warm Up Cache

Run this BEFORE stopping the registry service.
This simulates an application starting up and fetching prompts.
"""

import os

# Point to staging endpoint
os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "https://api.datad0g.com"

from ddtrace.llmobs._prompts.manager import PromptManager

# Use DD_API_KEY and DD_APP_KEY from environment (via dd-auth)
API_KEY = os.environ.get("DD_API_KEY", "test-api-key")
APP_KEY = os.environ.get("DD_APP_KEY")

manager = PromptManager(api_key=API_KEY, app_key=APP_KEY, site="datad0g.com", ml_app="customer-chatbot")

print("Application starting up... fetching prompts from registry")
prompt = manager.get_prompt("greeting", label="prod")
print(f"Got prompt: {prompt.template[:50]}...")
print()
print("Application is now serving customers happily...")
print(prompt.format(name="Customer 1", company="Acme"))
