#!/usr/bin/env python3
"""
Demo Part 2: Chat Template

Shows fetching a multi-turn conversation template (system + user messages).
"""

import os

# Point to staging endpoint
os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "https://api.datad0g.com"

from ddtrace.llmobs._prompts.manager import PromptManager

# Use DD_API_KEY and DD_APP_KEY from environment (via dd-auth)
API_KEY = os.environ.get("DD_API_KEY", "test-api-key")
APP_KEY = os.environ.get("DD_APP_KEY")

manager = PromptManager(api_key=API_KEY, app_key=APP_KEY, site="datad0g.com", ml_app="customer-chatbot")
prompt = manager.get_prompt("assistant", label="prod")

print("Chat template with multiple roles:")
for msg in prompt.template:
    print(f"  [{msg['role']}]: {msg['content'][:50]}...")
print()

rendered = prompt.format(
    company="TechCorp",
    assistant_name="Aria",
    user_message="How do I reset my password?",
)
print("Rendered conversation:")
for msg in rendered:
    print(f"  {msg['role'].upper()}: {msg['content']}")
