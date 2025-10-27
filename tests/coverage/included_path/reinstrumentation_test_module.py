"""
Simple test module for testing coverage re-instrumentation across contexts.

This module provides simple, predictable functions with known line numbers
to help test that coverage collection works correctly across multiple contexts.
"""


def simple_function(x, y):
    """A simple function with a few lines."""
    result = x + y
    return result


def function_with_loop(n):
    """A function with a loop to test repeated line execution."""
    total = 0
    for i in range(n):
        total += i
    return total


def function_with_branches(condition):
    """A function with branches to test different code paths."""
    if condition:
        result = "true_branch"
    else:
        result = "false_branch"
    return result


def multi_line_function(a, b, c):
    """A function with multiple lines to test comprehensive coverage."""
    step1 = a + b
    step2 = step1 * c
    step3 = step2 - a
    step4 = step3 / (b if b != 0 else 1)
    result = step4**2
    return result
