"""Textual-based terminal UI for runtime instrumentation.

Provides a live dashboard showing all instrumentable callables with their
call counts and instrumentation status. Allows users to instrument callables
at runtime via text input.
"""

import asyncio
from typing import TYPE_CHECKING

from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.containers import Vertical
from textual.widgets import DataTable
from textual.widgets import Footer
from textual.widgets import Header
from textual.widgets import Input
from textual.widgets import Static


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.controller.instrumentation import Instrumenter
    from tasks.runtime_instrumentation_poc.shared.state import SharedState


class InstrumentationUI(App):
    """Textual UI for runtime instrumentation PoC.

    Features:
    - Live table showing all callables with call counts and instrumentation status
    - Input field for instrumenting callables by qualified name
    - Status bar with color-coded feedback
    - Keyboard shortcuts for navigation
    """

    CSS = """
    #table-container {
        height: 1fr;
        border: solid $accent;
    }

    #status {
        height: 3;
        background: $panel;
        padding: 1;
    }

    #input-container {
        height: 3;
        border: solid $success;
    }

    DataTable {
        height: 100%;
    }

    .status-success {
        background: $success;
        color: $text;
    }

    .status-error {
        background: $error;
        color: $text;
    }

    .status-processing {
        background: $warning;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("i", "focus_input", "Instrument"),
        Binding("escape", "unfocus_input", "Cancel"),
    ]

    def __init__(self, shared_state: "SharedState", instrumenter: "Instrumenter") -> None:
        """Initialize UI.

        Args:
            shared_state: Shared state for reading callable data
            instrumenter: Instrumenter for applying bytecode patches
        """
        super().__init__()
        self.shared_state = shared_state
        self.instrumenter = instrumenter
        self._update_task: asyncio.Task = None  # type: ignore

    def compose(self) -> ComposeResult:
        """Create UI layout.

        Yields:
            UI components
        """
        yield Header()

        with Vertical():
            # Main table showing callables
            with Container(id="table-container"):
                yield DataTable(id="callables-table", cursor_type="row")

            # Status message area
            yield Static(
                "Ready. Press 'i' to instrument a callable, 'q' to quit.",
                id="status",
            )

            # Input field for instrumentation commands
            with Container(id="input-container"):
                yield Input(
                    placeholder=(
                        "Enter callable path (e.g., "
                        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1)"
                    ),
                    id="instrument-input",
                )

        yield Footer()

    def on_mount(self) -> None:
        """Initialize UI after mounting."""
        # Setup table
        table = self.query_one("#callables-table", DataTable)
        table.add_columns("Qualified Name", "Calls", "Instrumented", "Status")

        # Start update task
        self._update_task = asyncio.create_task(self._update_loop())

    async def _update_loop(self) -> None:
        """Continuously update table with latest data.

        Updates the table every 100ms (10Hz) with a snapshot of the shared state.
        """
        while True:
            try:
                # Get snapshot from shared state
                snapshot = self.shared_state.get_snapshot()

                # Update table
                table = self.query_one("#callables-table", DataTable)

                # Clear and repopulate (simple approach for ~20 rows)
                table.clear()
                for entry in snapshot:
                    # Shorten qualified name for display
                    short_name = entry["qualified_name"].replace(
                        "tasks.runtime_instrumentation_poc.target_app.dummy_modules.",
                        "",
                    )

                    table.add_row(
                        short_name,
                        str(entry["call_count"]),
                        str(entry["instrumentation_count"]),
                        "✓" if entry["is_instrumented"] else " ",
                    )

                # Refresh UI
                self.refresh()

                # Update every 100ms (10Hz)
                await asyncio.sleep(0.1)

            except Exception as e:
                self._update_status(f"Error updating table: {e}", "error")
                await asyncio.sleep(1.0)  # Back off on errors

    def action_focus_input(self) -> None:
        """Focus the input field (keyboard shortcut 'i')."""
        input_widget = self.query_one("#instrument-input", Input)
        input_widget.focus()

    def action_unfocus_input(self) -> None:
        """Unfocus the input field (keyboard shortcut 'escape')."""
        input_widget = self.query_one("#instrument-input", Input)
        input_widget.blur()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user submitting a callable path to instrument.

        Args:
            event: Input submission event
        """
        path = event.value.strip()

        if not path:
            return

        # Clear input
        event.input.value = ""

        # Show processing status
        self._update_status(f"Instrumenting {path}...", "processing")

        # Perform instrumentation in background thread
        # (inject_hook modifies bytecode, shouldn't block UI)
        try:
            success, message = await asyncio.to_thread(self.instrumenter.instrument_callable, path)

            # Update status based on result
            if success:
                self._update_status(message, "success")
            else:
                self._update_status(message, "error")

        except Exception as e:
            self._update_status(f"Unexpected error: {e}", "error")

    def _update_status(self, message: str, status_type: str) -> None:
        """Update the status message with color coding.

        Args:
            message: Status message to display
            status_type: Type of status ("success", "error", "processing", or "normal")
        """
        status_widget = self.query_one("#status", Static)

        # Apply CSS class based on status type
        status_widget.remove_class("status-success", "status-error", "status-processing")

        if status_type == "success":
            status_widget.add_class("status-success")
        elif status_type == "error":
            status_widget.add_class("status-error")
        elif status_type == "processing":
            status_widget.add_class("status-processing")

        status_widget.update(message)
