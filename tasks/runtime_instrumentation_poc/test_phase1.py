"""Phase 1 test: Verify basic execution and call counting.

This test script initializes the target app with module_a callables
and runs for a few seconds to verify that call counts are increasing.
"""

import asyncio
from pathlib import Path
import sys


# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Imports after sys.path manipulation (intentional for PoC)
from tasks.runtime_instrumentation_poc.shared.state import SharedState  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.dummy_modules import module_a  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.main import TargetApp  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.registry import CallableRegistry  # noqa: E402


def initialize_registry() -> CallableRegistry:
    """Build registry with all callables from module_a."""
    registry = CallableRegistry()

    # Register functions
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1",
        module_a.func_a1,
    )
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a2",
        module_a.func_a2,
    )
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a3",
        module_a.func_a3,
    )

    # Register ClassA1 methods
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.method_a1",
        module_a.ClassA1,
        "method_a1",
    )
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.method_a2",
        module_a.ClassA1,
        "method_a2",
    )
    registry.register_staticmethod(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.static_a1",
        module_a.ClassA1,
        "static_a1",
    )
    registry.register_classmethod(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.class_a1",
        module_a.ClassA1,
        "class_a1",
    )

    # Register ClassA2 methods
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA2.method_a3",
        module_a.ClassA2,
        "method_a3",
    )
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA2.method_a4",
        module_a.ClassA2,
        "method_a4",
    )

    return registry


def initialize_shared_state(registry: CallableRegistry) -> SharedState:
    """Initialize SharedState with all callables from registry."""
    shared_state = SharedState()

    for qualified_name, callable_obj in registry.get_all_entries():
        shared_state.register_callable(qualified_name, callable_obj)

    return shared_state


async def main():
    """Test Phase 1: Basic execution and call counting."""
    print("Phase 1 Test: Verifying call counts increase\n")

    # Initialize components
    registry = initialize_registry()
    shared_state = initialize_shared_state(registry)

    # Set shared state in module_a so callables can update counters
    module_a.set_shared_state(shared_state)

    # Create target app
    target_app = TargetApp(registry, shared_state)

    print(f"Registered {len(registry)} callables")
    print("Starting execution loop for 3 seconds...\n")

    # Run target app for 3 seconds
    app_task = asyncio.create_task(target_app.run_loop())

    try:
        await asyncio.sleep(3.0)
    finally:
        target_app.stop()
        app_task.cancel()
        try:
            await app_task
        except asyncio.CancelledError:
            pass

    # Display results
    print("\nResults:")
    print("-" * 80)
    snapshot = shared_state.get_snapshot()

    for entry in snapshot:
        print(f"{entry['qualified_name']:<70} Calls: {entry['call_count']:>5}")

    total_calls = sum(e["call_count"] for e in snapshot)
    print("-" * 80)
    print(f"{'Total':70} Calls: {total_calls:>5}")

    # Verify
    if total_calls > 0:
        print("\n✓ Phase 1 PASSED: Call counts are increasing!")
        return 0
    else:
        print("\n✗ Phase 1 FAILED: No calls recorded")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
