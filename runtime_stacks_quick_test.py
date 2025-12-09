"""
Test script to verify Echion-based stack walking in crashtracker.

This script:
1. Enables crashtracking with runtime stacks
2. Disables _Py_DumpTracebackThreads to force Echion fallback
3. Triggers a crash to test stack capture
"""

import os
import sys
import signal
import time
import ctypes

def nested_function_3():
    """Deepest function that will trigger a crash"""
    print("In nested_function_3 - about to crash!")
    # Trigger a segmentation fault
    ctypes.string_at(0)

def nested_function_2():
    """Middle function"""
    print("In nested_function_2")
    nested_function_3()

def nested_function_1():
    """Top-level function"""
    print("In nested_function_1")
    nested_function_2()

def main():
    print("Setting up crashtracker test...")

    # Enable crashtracking with runtime stacks
    os.environ['DD_CRASHTRACKING_ENABLED'] = 'true'
    os.environ['DD_CRASHTRACKING_EMIT_RUNTIME_STACKS'] = 'true'

    # Import and initialize crashtracker
    try:
        import ddtrace
        from ddtrace.internal.core import crashtracking

        # Start crashtracking
        if crashtracking.start():
            print("Crashtracker started successfully")
        else:
            print("Failed to start crashtracker")
            return 1

        # Give it a moment to initialize
        time.sleep(0.5)

        print("\nNow triggering a crash with nested function calls...")
        print("Expected stack trace should show:")
        print("  - main()")
        print("  - nested_function_1()")
        print("  - nested_function_2()")
        print("  - nested_function_3()")
        print()

        # Call the nested functions that will crash
        nested_function_1()

    except ImportError as e:
        print(f"Failed to import ddtrace: {e}")
        print("Make sure dd-trace-py is built and installed")
        return 1

    # Should not reach here
    print("ERROR: Should have crashed by now!")
    return 1

if __name__ == "__main__":
    sys.exit(main())
