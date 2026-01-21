#!/usr/bin/env python3
"""
Demo Part 2: Chat Template

Shows fetching a multi-turn conversation template (system + user messages).
"""

import os


# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "session-summary-eval")

from ddtrace.llmobs import LLMObs


prompt = LLMObs.get_prompt("summary", label="prod")

print("Chat template with multiple roles:")
for msg in prompt.template:
    content_preview = msg['content'][:80].replace('\n', ' ')
    print(f"  [{msg['role']}]: {content_preview}...")
print()
print(f"Variables: {prompt.variables}")
print()

rendered = prompt.format(event_context="view,/home,0\naction,click-button,1000")
print("Rendered conversation (first message truncated):")
for msg in rendered:
    content_preview = msg['content'][:100].replace('\n', ' ')
    print(f"  {msg['role'].upper()}: {content_preview}...")
