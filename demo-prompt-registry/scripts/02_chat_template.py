#!/usr/bin/env python3
"""
Demo Part 2: Chat Template

Shows fetching a multi-turn conversation template (system + user messages).
"""

import os


os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "http://localhost:8080"

from ddtrace.llmobs._prompts.manager import PromptManager


manager = PromptManager(api_key="my-api-key", site="datadoghq.com", ml_app="customer-chatbot")
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
