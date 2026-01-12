#!/usr/bin/env python3
"""
Demo Part 4: Caching Performance

Shows the speed difference between cold cache (network fetch) and hot cache (instant).
"""

import os
import time


os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "http://localhost:8080"

from ddtrace.llmobs._prompts.manager import PromptManager


# Disable file cache to show true cold start behavior
manager = PromptManager(
    api_key="my-api-key",
    site="datadoghq.com",
    ml_app="caching-demo",
    file_cache_enabled=False,
)

print("First call (cold - network fetch from registry):")
start = time.time()
p1 = manager.get_prompt("greeting", label="prod")
elapsed1 = (time.time() - start) * 1000
print(f"  Time: {elapsed1:.1f}ms | Source: {p1.source}")

print()
print("Second call (hot - from memory cache):")
start = time.time()
p2 = manager.get_prompt("greeting", label="prod")
elapsed2 = (time.time() - start) * 1000
print(f"  Time: {elapsed2:.3f}ms | Source: {p2.source}")

print()
print(f"Speed improvement: {elapsed1 / elapsed2:.0f}x faster from cache")
print()
print("Your AI can handle thousands of requests per second.")
print("Prompts are cached locally and refreshed in the background.")
