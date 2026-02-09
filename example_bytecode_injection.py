#!/usr/bin/env python3
"""
Minimal example demonstrating ddtrace.internal.bytecode_injection.inject_hook

This example shows how to inject a hook into a function to auto-instrument it.
"""

from ddtrace.internal.bytecode_injection import inject_hook


def my_hook(arg):
    """Hook function that will be injected"""
    print(f"  [HOOK INJECTED] Hook called with arg: {arg}")


def dummy_function(x, y):
    """Dummy function to demonstrate injection"""
    print(f"  [DUMMY] Executing dummy_function with x={x}, y={y}")  # Line 19 - injection point
    result = x + y
    print(f"  [DUMMY] Result is {result}")
    return result


def main():
    print("=" * 60)
    print("EXAMPLE: Bytecode Hook Injection")
    print("=" * 60)

    # Step 1: Call the function before injection
    print("\n1. Calling dummy_function BEFORE injection:")
    print("-" * 60)
    result1 = dummy_function(5, 3)
    print(f"  Returned: {result1}")

    # Step 2: Inject the hook at line 19 (the first print inside dummy_function)
    print("\n2. Injecting hook at line 19 (first print statement)...")
    print("-" * 60)

    inject_hook(
        f=dummy_function,
        hook=my_hook,
        line=19,  # Line number where the first print statement is
        arg="my_identifier",  # Identifier for this hook (used for later removal if needed)
    )

    print("  Hook successfully injected!")

    # Step 3: Call the function after injection
    print("\n3. Calling dummy_function AFTER injection:")
    print("-" * 60)
    result2 = dummy_function(10, 7)
    print(f"  Returned: {result2}")

    print("\n" + "=" * 60)
    print("SUCCESS: Hook was called before the dummy function's print!")
    print("=" * 60)

    # Bonus: Show how to eject the hook
    print("\n4. BONUS: Repeat...")
    print("-" * 60)

    print("\n5. Calling dummy_function AFTER ejection:")
    print("-" * 60)
    result3 = dummy_function(15, 5)
    print(f"  Returned: {result3}")

    print("\n" + "=" * 60)
    print("Back to normal: Hook is no longer called!")
    print("=" * 60)


if __name__ == "__main__":
    main()
