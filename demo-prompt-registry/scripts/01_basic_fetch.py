#!/usr/bin/env python3
"""
Demo Part 1: Basic Prompt Fetch

Shows fetching a text template prompt and rendering it with variables.
"""

import os

# Point to staging endpoint
os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "https://api.datad0g.com"

from ddtrace.llmobs._prompts.manager import PromptManager

# Use DD_API_KEY and DD_APP_KEY from environment (via dd-auth)
API_KEY = os.environ.get("DD_API_KEY", "test-api-key")
APP_KEY = os.environ.get("DD_APP_KEY")

manager = PromptManager(api_key=API_KEY, app_key=APP_KEY, site="datad0g.com", ml_app="customer-chatbot")
prompt = manager.get_prompt("greeting", label="prod")

print("Fetched prompt from registry!")
print(f"Template: {prompt.template}")
print()
print("Now rendering for a real customer...")
print(prompt.format(name="Sarah", company="Acme Inc"))
