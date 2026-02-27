#!/usr/bin/env python
"""
LBYL (Look Before You Leap) approach:
Check if the key exists before accessing it.
"""

import random
import time


# Dictionary that may or may not have the key
data = {}


def main():
    time_start = time.time()
    iteration = 0
    hits = 0
    misses = 0

    while True:
        iteration += 1
        if random.random() < 0.5:
            data["target_key"] = "value"
        else:
            data.pop("target_key", None)
        if "target_key" in data:
            _ = data["target_key"]
            hits += 1
        else:
            misses += 1

        if iteration == 1_000_000_000:
            break
    time_end = time.time()
    print(f"Time: {time_end - time_start:.3f}s")

if __name__ == "__main__":
    main()
