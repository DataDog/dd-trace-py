#!/usr/bin/env python3
"""
Demo Part 4: Caching Performance

Shows the speed difference between cold cache (network fetch) and hot cache (instant).
"""

import os
import time

# Configure environment for staging (DD_API_KEY should be set via dd-auth)
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "https://api.datad0g.com")
os.environ.setdefault("DD_LLMOBS_ML_APP", "caching-demo")

from ddtrace.llmobs import LLMObs

# Clear cache to show true cold start behavior
LLMObs.clear_prompt_cache(l1=True, l2=True)

print("First call (cold - network fetch from registry):")
start = time.time()
p1 = LLMObs.get_prompt("greeting", label="prod")
elapsed1 = (time.time() - start) * 1000
print(f"  Time: {elapsed1:.1f}ms | Source: {p1.source}")

print()
NUM_ITERATIONS = 100
print(f"Hot cache ({NUM_ITERATIONS} calls - from memory cache):")
times = []
for _ in range(NUM_ITERATIONS):
    start = time.time()
    p2 = LLMObs.get_prompt("greeting", label="prod")
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
