"""Main entry point for runtime instrumentation PoC.

This script initializes all components and runs the target application
alongside the Textual UI, demonstrating runtime bytecode instrumentation.
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
from tasks.runtime_instrumentation_poc.controller.ui import InstrumentationUI  # noqa: E402
from tasks.runtime_instrumentation_poc.shared.state import SharedState  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.dummy_modules import module_a  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.dummy_modules import module_b  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.dummy_modules import module_c  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.dummy_modules import module_d  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.main import TargetApp  # noqa: E402
from tasks.runtime_instrumentation_poc.target_app.registry import CallableRegistry  # noqa: E402


# Configure logging
logging.basicConfig(
    level=logging.WARNING,  # Only show warnings and errors
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def initialize_registry() -> CallableRegistry:
    """Build registry of all instrumentable callables.

    Returns:
        Populated CallableRegistry
    """
    registry = CallableRegistry()

    # Register functions from module_a
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

    # Register functions from module_b
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:func_b1",
        module_b.func_b1,
    )
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:func_b2",
        module_b.func_b2,
    )

    # Register ClassB1 methods
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:ClassB1.method_b1",
        module_b.ClassB1,
        "method_b1",
    )
    registry.register_staticmethod(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:ClassB1.static_b1",
        module_b.ClassB1,
        "static_b1",
    )

    # Register functions from module_c
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:func_c1",
        module_c.func_c1,
    )
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:func_c2",
        module_c.func_c2,
    )

    # Register ClassC1 methods
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:ClassC1.method_c1",
        module_c.ClassC1,
        "method_c1",
    )
    registry.register_classmethod(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:ClassC1.class_c1",
        module_c.ClassC1,
        "class_c1",
    )

    # Register functions from module_d
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:func_d1",
        module_d.func_d1,
    )
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:func_d2",
        module_d.func_d2,
    )
    registry.register_function(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:func_d3",
        module_d.func_d3,
    )

    # Register ClassD1 methods
    registry.register_method(
        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:ClassD1.method_d1",
        module_d.ClassD1,
        "method_d1",
    )

    return registry


def initialize_shared_state(registry: CallableRegistry) -> SharedState:
    """Initialize SharedState with all callables from registry.

    Args:
        registry: Registry containing all callables

    Returns:
        Initialized SharedState
    """
    shared_state = SharedState()

    for qualified_name, callable_obj in registry.get_all_entries():
        shared_state.register_callable(qualified_name, callable_obj)

    return shared_state


async def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success)
    """
    # Initialize components
    registry = initialize_registry()
    shared_state = initialize_shared_state(registry)
    instrumenter = Instrumenter(shared_state)

    # Set shared state in all modules so callables can update counters
    module_a.set_shared_state(shared_state)
    module_b.set_shared_state(shared_state)
    module_c.set_shared_state(shared_state)
    module_d.set_shared_state(shared_state)

    # Create target app
    target_app = TargetApp(registry, shared_state)

    # Create UI
    ui = InstrumentationUI(shared_state, instrumenter)

    # Run both concurrently
    # Target app loop runs as a background task
    # UI app takes over with run_async()
    app_task = asyncio.create_task(target_app.run_loop())

    try:
        await ui.run_async()
    finally:
        # Cleanup
        target_app.stop()
        app_task.cancel()
        try:
            await app_task
        except asyncio.CancelledError:
            pass

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
