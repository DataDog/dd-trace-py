"""Phase 2 test: Verify bytecode instrumentation works.

This test script runs the target app, instruments a callable at runtime,
and verifies that the instrumentation counter increases.
"""

import asyncio
import logging
from pathlib import Path
import sys


# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Imports after sys.path manipulation (intentional for PoC)
from tasks.runtime_instrumentation_poc.controller.instrumentation import Instrumenter  # noqa: E402
from tasks.runtime_instrumentation_poc.shared.state import SharedState  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.dummy_modules import module_a  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.main import TargetApp  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.registry import CallableRegistry  # noqa: E402


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


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
    """Test Phase 2: Bytecode instrumentation."""
    print("Phase 2 Test: Verifying bytecode instrumentation\n")

    # Initialize components
    registry = initialize_registry()
    shared_state = initialize_shared_state(registry)
    instrumenter = Instrumenter(shared_state)

    # Set shared state in module_a so callables can update counters
    module_a.set_shared_state(shared_state)

    # Create target app
    target_app = TargetApp(registry, shared_state)

    print(f"Registered {len(registry)} callables")
    print("Starting execution loop...\n")

    # Start target app
    app_task = asyncio.create_task(target_app.run_loop())

    try:
        # Run for 2 seconds without instrumentation
        print("Phase 1: Running without instrumentation for 2 seconds...")
        await asyncio.sleep(2.0)

        snapshot = shared_state.get_snapshot()
        func_a1_before = next(e for e in snapshot if e["qualified_name"].endswith("func_a1"))

        print(
            f"  func_a1 - Calls: {func_a1_before['call_count']}, "
            f"Instrumented: {func_a1_before['instrumentation_count']}, "
            f"Status: {'✓' if func_a1_before['is_instrumented'] else '✗'}"
        )

        # Instrument func_a1
        print("\nPhase 2: Instrumenting func_a1...")
        target_name = "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1"
        success, message = instrumenter.instrument_callable(target_name)

        if not success:
            print(f"  ✗ Instrumentation failed: {message}")
            return 1

        print(f"  ✓ {message}")

        # Run for 2 more seconds with instrumentation
        print("\nPhase 3: Running with instrumentation for 2 seconds...")
        await asyncio.sleep(2.0)

        snapshot = shared_state.get_snapshot()
        func_a1_after = next(e for e in snapshot if e["qualified_name"].endswith("func_a1"))

        print(
            f"  func_a1 - Calls: {func_a1_after['call_count']}, "
            f"Instrumented: {func_a1_after['instrumentation_count']}, "
            f"Status: {'✓' if func_a1_after['is_instrumented'] else '✗'}"
        )

        # Verify instrumentation worked
        print("\nResults:")
        print("-" * 80)

        call_count_increased = func_a1_after["call_count"] > func_a1_before["call_count"]
        instrumentation_count_increased = func_a1_after["instrumentation_count"] > 0
        is_marked_instrumented = func_a1_after["is_instrumented"]

        print(f"Call count increased: {call_count_increased}")
        print(
            f"Instrumentation count > 0: {instrumentation_count_increased} "
            f"(value: {func_a1_after['instrumentation_count']})"
        )
        print(f"Marked as instrumented: {is_marked_instrumented}")

        if call_count_increased and instrumentation_count_increased and is_marked_instrumented:
            print("\n✓ Phase 2 PASSED: Bytecode instrumentation works!")
            return 0
        else:
            print("\n✗ Phase 2 FAILED: Instrumentation did not work correctly")
            return 1

    finally:
        target_app.stop()
        app_task.cancel()
        try:
            await app_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
