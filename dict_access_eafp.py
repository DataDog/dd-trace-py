#!/usr/bin/env python
"""
EAFP (Easier to Ask for Forgiveness than Permission) approach:
Try to access the key and catch the exception if it doesn't exist.
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

        # 50% chance the key exists
        if random.random() < 0.5:
            data["target_key"] = "value"
        else:
            data.pop("target_key", None)

        # EAFP: Try to access, catch exception if missing
        try:
            _ = data["target_key"]
            hits += 1
        except KeyError:
            misses += 1

        if iteration == 1_000_000_000:
            break
    time_end = time.time()
    print(f"Time: {time_end - time_start:.3f}s")

if __name__ == "__main__":
    main()
