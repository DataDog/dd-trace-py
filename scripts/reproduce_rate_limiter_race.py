"""Reproducer for BudgetRateLimiterWithJitter race condition.

The bug: `self.budget -= 1.0` happens OUTSIDE the lock in `limit()`.
Two threads can both observe budget >= 1.0, both decide should_call=True,
then both decrement the budget outside the lock — allowing more calls than
the budget permits and driving the budget negative.

Run this script BEFORE the fix to see the bug, and AFTER the fix to see it resolved.

Usage:
    python scripts/reproduce_rate_limiter_race.py
"""

import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------- Minimal copy of the BUGGY rate limiter (before fix) ----------
class RateLimitExceeded(Exception):
    pass


@dataclass
class BuggyBudgetRateLimiterWithJitter:
    """BUGGY version — budget decrement outside the lock."""

    limit_rate: float
    tau: float = 1.0
    raise_on_exceed: bool = True
    on_exceed: Optional[Callable] = None
    call_once: bool = False
    budget: float = field(init=False)
    max_budget: float = field(init=False)
    last_time: float = field(init=False, default_factory=time.monotonic)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self):
        if self.limit_rate == float("inf"):
            self.budget = self.max_budget = float("inf")
        elif self.limit_rate:
            self.budget = self.max_budget = self.limit_rate * self.tau
        else:
            self.budget = self.max_budget = 1.0
        self._on_exceed_called = False

    def limit(self, f=None, *args, **kwargs):
        should_call = False
        with self._lock:
            now = time.monotonic()
            self.budget += self.limit_rate * (now - self.last_time) * (0.5 + random.random())
            should_call = self.budget >= 1.0
            if self.budget > self.max_budget:
                self.budget = self.max_budget
            self.last_time = now

        # BUG: budget decrement and flag reset happen OUTSIDE the lock.
        # The time.sleep(0) widens the race window for demonstration purposes;
        # in production the window is smaller but still exploitable under load.
        if should_call:
            self._on_exceed_called = False
            time.sleep(0)
            self.budget -= 1.0
            return f(*args, **kwargs) if f is not None else None

        if self.on_exceed is not None:
            if not self.call_once:
                self.on_exceed()
            elif not self._on_exceed_called:
                self.on_exceed()
                self._on_exceed_called = True

        if self.raise_on_exceed:
            raise RateLimitExceeded()
        else:
            return RateLimitExceeded


# ---------- Minimal copy of the FIXED rate limiter ----------
@dataclass
class FixedBudgetRateLimiterWithJitter:
    """FIXED version — all shared state mutations inside the lock."""

    limit_rate: float
    tau: float = 1.0
    raise_on_exceed: bool = True
    on_exceed: Optional[Callable] = None
    call_once: bool = False
    budget: float = field(init=False)
    max_budget: float = field(init=False)
    last_time: float = field(init=False, default_factory=time.monotonic)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self):
        if self.limit_rate == float("inf"):
            self.budget = self.max_budget = float("inf")
        elif self.limit_rate:
            self.budget = self.max_budget = self.limit_rate * self.tau
        else:
            self.budget = self.max_budget = 1.0
        self._on_exceed_called = False

    def limit(self, f=None, *args, **kwargs):
        should_call = False
        should_notify = False
        with self._lock:
            now = time.monotonic()
            self.budget += self.limit_rate * (now - self.last_time) * (0.5 + random.random())
            should_call = self.budget >= 1.0
            if should_call:
                self.budget -= 1.0
                self._on_exceed_called = False
            if self.budget > self.max_budget:
                self.budget = self.max_budget
            self.last_time = now

            if not should_call and self.on_exceed is not None:
                if not self.call_once:
                    should_notify = True
                elif not self._on_exceed_called:
                    should_notify = True
                    self._on_exceed_called = True

        if should_call:
            return f(*args, **kwargs) if f is not None else None

        if should_notify:
            self.on_exceed()

        if self.raise_on_exceed:
            raise RateLimitExceeded()
        else:
            return RateLimitExceeded


def run_concurrent_test(limiter_class, label, num_threads=8, iterations=200, total_trials=20):
    """Run concurrent calls and count how many slip through the rate limit."""
    races_detected = 0

    for trial in range(total_trials):
        limiter = limiter_class(limit_rate=1, tau=1.0, raise_on_exceed=False)
        call_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def worker():
            nonlocal call_count
            barrier.wait()
            for _ in range(iterations):
                result = limiter.limit(lambda: "ok")
                if result == "ok":
                    with count_lock:
                        call_count += 1

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # With limit_rate=1 and tau=1, the initial budget is 1.0.
        # In a tight loop, the time delta is near-zero, so almost no budget
        # is replenished. The max allowed calls should be close to 1 (initial
        # budget). With the race, many more calls slip through.
        if call_count > 2:
            races_detected += 1
            if races_detected <= 5:
                print(f"  [{label}] Trial {trial + 1}: {call_count} calls allowed (expected ~1)")

    return races_detected


def run_negative_budget_test(limiter_class, label, num_threads=8, iterations=100, total_trials=20):
    """Check if budget goes negative due to unsynchronized decrement."""
    negative_detected = False

    for trial in range(total_trials):
        limiter = limiter_class(limit_rate=1, tau=1.0, raise_on_exceed=False)
        barrier = threading.Barrier(num_threads)
        min_budget = float("inf")
        budget_lock = threading.Lock()

        def worker():
            nonlocal min_budget
            barrier.wait()
            for _ in range(iterations):
                limiter.limit(lambda: None)
                with budget_lock:
                    if limiter.budget < min_budget:
                        min_budget = limiter.budget

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if min_budget < 0:
            negative_detected = True
            if not negative_detected:
                print(f"  [{label}] Trial {trial + 1}: budget went to {min_budget:.2f}")

    return negative_detected


if __name__ == "__main__":
    print("=" * 70)
    print("Test 1: Concurrent threads exceed rate limit — BUGGY version")
    print("=" * 70)
    buggy_races = run_concurrent_test(BuggyBudgetRateLimiterWithJitter, "BUGGY")
    if buggy_races:
        print(f"\n*** BUG CONFIRMED: {buggy_races}/20 trials had excess calls ***")
    else:
        print("\n  (Race not triggered in this run — try again, it's non-deterministic)")

    print()
    print("=" * 70)
    print("Test 2: Concurrent threads exceed rate limit — FIXED version")
    print("=" * 70)
    fixed_races = run_concurrent_test(FixedBudgetRateLimiterWithJitter, "FIXED")
    if fixed_races:
        print(f"\n  Unexpected: {fixed_races}/20 trials had excess calls")
    else:
        print("\n  All 20 trials passed — fix prevents the race condition.")

    print()
    print("=" * 70)
    print("Test 3: Budget goes negative — BUGGY version")
    print("=" * 70)
    buggy_neg = run_negative_budget_test(BuggyBudgetRateLimiterWithJitter, "BUGGY")
    if buggy_neg:
        print("\n*** BUG CONFIRMED: Budget went negative ***")
    else:
        print("\n  (Negative budget not triggered — try again)")

    print()
    print("=" * 70)
    print("Test 4: Budget goes negative — FIXED version")
    print("=" * 70)
    fixed_neg = run_negative_budget_test(FixedBudgetRateLimiterWithJitter, "FIXED")
    if fixed_neg:
        print("\n  Unexpected: budget went negative in fixed version")
    else:
        print("\n  Budget stayed non-negative — fix works correctly.")

    print()
    print("=" * 70)
    if buggy_races or buggy_neg:
        print("RESULT: Race condition CONFIRMED in buggy version, FIXED version is correct.")
    else:
        print("RESULT: Race not triggered this run (non-deterministic). Try running again.")
    sys.exit(0)
