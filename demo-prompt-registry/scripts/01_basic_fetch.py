#!/usr/bin/env python3
"""
Demo Part 1: Basic Prompt Fetch

Shows fetching a text template prompt and rendering it with variables.
"""

import os


os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "http://localhost:8080"

from ddtrace.llmobs._prompts.manager import PromptManager


manager = PromptManager(api_key="my-api-key", site="datadoghq.com", ml_app="customer-chatbot")
prompt = manager.get_prompt("greeting", label="prod")

print("Fetched prompt from registry!")
print(f"Template: {prompt.template}")
print()
print("Now rendering for a real customer...")
print(prompt.format(name="Sarah", company="Acme Inc"))
