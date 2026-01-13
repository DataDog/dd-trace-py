#!/usr/bin/env python3
"""
Demo Part 4: Caching Performance

Shows the speed difference between cold cache (network fetch) and hot cache (instant).
"""

import os
import time

# Point to staging endpoint
os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "https://api.datad0g.com"

from ddtrace.llmobs._prompts.manager import PromptManager

# Use DD_API_KEY and DD_APP_KEY from environment (via dd-auth)
API_KEY = os.environ.get("DD_API_KEY", "test-api-key")
APP_KEY = os.environ.get("DD_APP_KEY")

# Disable file cache to show true cold start behavior
manager = PromptManager(
    api_key=API_KEY,
    app_key=APP_KEY,
    site="datad0g.com",
    ml_app="caching-demo",
    file_cache_enabled=False,
)

print("First call (cold - network fetch from registry):")
start = time.time()
p1 = manager.get_prompt("greeting", label="prod")
elapsed1 = (time.time() - start) * 1000
print(f"  Time: {elapsed1:.1f}ms | Source: {p1.source}")

print()
NUM_ITERATIONS = 100
print(f"Hot cache ({NUM_ITERATIONS} calls - from memory cache):")
times = []
for _ in range(NUM_ITERATIONS):
    start = time.time()
    p2 = manager.get_prompt("greeting", label="prod")
    times.append((time.time() - start) * 1000)

avg_time = sum(times) / len(times)
min_time = min(times)
max_time = max(times)
print(f"  Average: {avg_time:.4f}ms | Min: {min_time:.4f}ms | Max: {max_time:.4f}ms")

print()
print(f"Speed improvement: {elapsed1 / avg_time:.0f}x faster from cache")
print(f"Throughput: ~{1000 / avg_time:,.0f} prompt fetches/second")
print()
print("Your AI can handle thousands of requests per second.")
print("Prompts are cached locally and refreshed in the background.")
