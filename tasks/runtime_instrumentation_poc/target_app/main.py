"""Target application with continuous execution loop.

The target app continuously calls random functions/methods from the registry,
demonstrating the instrumentation working in real-time.
"""

import asyncio
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.shared.state import SharedState
    from tasks.runtime_instrumentation_poc.target_app.registry import CallableRegistry


logger = logging.getLogger(__name__)


class TargetApp:
    """Target application that executes dummy callables continuously.

    This simulates a real application that would be instrumented. The app runs
    in a loop, randomly selecting and calling functions/methods from the registry.
    """

    def __init__(self, registry: "CallableRegistry", shared_state: "SharedState") -> None:
        """Initialize target app.

        Args:
            registry: Registry of callables to invoke
            shared_state: Shared state for tracking (unused directly, but callables use it)
        """
        self.registry = registry
        self.shared_state = shared_state
        self._running = True

    async def run_loop(self) -> None:
        """Main execution loop - runs continuously.

        Randomly picks callables from the registry and invokes them.
        Rate-limited to ~100 calls/second for UI readability.

        Note:
            Errors during callable execution are logged but don't stop the loop.
            This ensures the target app continues running even if instrumentation
            introduces bugs.
        """
        logger.info("Target app execution loop starting...")

        while self._running:
            try:
                # Pick random callable
                qualified_name, callable_obj = self.registry.get_random_entry()

                # Call it (this increments call_count inside the callable)
                try:
                    callable_obj()
                except Exception as e:
                    # Log but don't crash - target app keeps running
                    logger.error("Error calling %s: %s", qualified_name, e)

                # Small sleep to keep UI readable (~100 calls/second)
                await asyncio.sleep(0.01)

            except Exception as e:
                logger.error("Error in main loop: %s", e)
                await asyncio.sleep(0.1)  # Back off on errors

        logger.info("Target app execution loop stopped.")

    def stop(self) -> None:
        """Stop the execution loop."""
        self._running = False
        logger.info("Target app stop requested.")
