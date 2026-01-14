#!/usr/bin/env python3
"""
Demo Part 2: Chat Template

Shows fetching a multi-turn conversation template (system + user messages).
"""

import os

# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "customer-chatbot")

from ddtrace.llmobs import LLMObs

prompt = LLMObs.get_prompt("assistant", label="prod")

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
