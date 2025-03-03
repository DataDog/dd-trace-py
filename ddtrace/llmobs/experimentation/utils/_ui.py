from enum import Enum
import os
import time
from typing import Optional

from .._config import MAX_PROGRESS_BAR_WIDTH


def _print_progress_bar(iteration, total, prefix="", suffix="", decimals=1, length=50, fill="█"):
    """
    Print a simple progress bar to the console (commonly used for chunked uploads).

    Args:
        iteration (int): Current iteration or chunk number.
        total (int): Total number of iterations or chunks.
        prefix (str, optional): Optional prefix string. Defaults to "".
        suffix (str, optional): Optional suffix string. Defaults to "".
        decimals (int, optional): Number of decimal places to display in the percentage. Defaults to 1.
        length (int, optional): Character length of the progress bar. Defaults to 50.
        fill (str, optional): Character used to fill the progress bar. Defaults to "█".
    """
    percent = f"{100 * (iteration / float(total)):.{decimals}f}"
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + "-" * (length - filled_length)
    print(f"\r{prefix} |{bar}| {percent}% {suffix}", end="\r", flush=True)
    if iteration == total:
        print()


class Color:
    """
    ANSI color codes for terminal output, which can be used to highlight or style text
    in logs or console applications.
    """

    GREY = "\033[90m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


class Spinner:
    """
    Contains patterns for displaying animated spinners in the console,
    used alongside progress or waiting states.
    """

    DOTS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    ARROW = ["▹▹▹▹▹", "▸▹▹▹▹", "▹▸▹▹▹", "▹▹▸▹▹", "▹▹▹▸▹", "▹▹▹▹▸"]


class ProgressState(Enum):
    """
    Enumeration indicating overall progress states for console animations or logs.

    Values:
        RUNNING: The progress is currently in progress.
        SUCCESS: The progress has completed successfully.
        ERROR: The progress encountered an error.
    """

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ProgressReporter:
    """
    An enhanced progress reporter with color-coded output and optional "spinner"
    animations, used for interactive console feedback during dataset processing.
    """

    def __init__(self, total: int, desc: str = "", width: Optional[int] = None) -> None:
        """
        Create a ProgressReporter to track completion and errors over a specified total.

        Args:
            total (int): The total number of items or iterations.
            desc (str, optional): A short description of what is being tracked. Defaults to "".
            width (int, optional): The width of the progress bar. If None, attempts to auto-detect terminal width.
        """
        self.total = total
        self.current = 0
        self.width: int = self._get_width(width)
        self.start_time = time.time()
        self.desc = desc
        self.error_count = 0
        self._last_len = 0
        self._last_update = self.start_time
        self._spinner_idx = 0
        self._state = ProgressState.RUNNING

    def _get_width(self, width: Optional[int]) -> int:
        """
        Determine the width of the progress bar, trying to respect terminal size if not specified.

        Args:
            width (int, optional): The desired progress bar width. Defaults to None.

        Returns:
            int: The width of the progress bar in characters.
        """
        if width is None:
            try:
                terminal_width = os.get_terminal_size().columns
                return min(MAX_PROGRESS_BAR_WIDTH, terminal_width - 50)
            except OSError:
                return MAX_PROGRESS_BAR_WIDTH
        return width

    def update(self, advance: int = 1, error: bool = False) -> None:
        """
        Update progress with optional error tracking.

        Args:
            advance (int, optional): Number of items completed since last update. Defaults to 1.
            error (bool, optional): Whether an error occurred. Defaults to False.
        """
        self.current += advance
        if error:
            self.error_count += 1
            self._state = ProgressState.ERROR

        # Throttle updates to max 15 per second
        now = time.time()
        if now - self._last_update < 0.066 and self.current < self.total:
            return

        self._last_update = now
        self._spinner_idx = (self._spinner_idx + 1) % len(Spinner.DOTS)
        self._print_progress()

    def _format_time(self, seconds: float) -> str:
        """
        Return a user-friendly time format (e.g., 13.2s, 2m 4s, 1h 15m).

        Args:
            seconds (float): Elapsed time in seconds.

        Returns:
            str: A formatted time string.
        """
        if seconds < 60:
            return f"{seconds:.1f}s"

        total_minutes = int(seconds / 60)
        remaining_seconds = seconds % 60

        if total_minutes < 60:
            return f"{total_minutes}m {int(remaining_seconds)}s"

        total_hours = int(total_minutes / 60)
        remaining_minutes = total_minutes % 60
        return f"{total_hours}h {remaining_minutes}m"

    def _format_speed(self) -> str:
        """
        Calculate and format processing speed.

        Returns:
            str: Formatted speed string.
        """
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return "0/s"
        speed = self.current / elapsed
        if speed >= 100:
            return f"{speed:.0f}/s"
        return f"{speed:.1f}/s"

    def _get_gradient_color(self, progress: float) -> str:
        """
        Return a color based on progress and state.

        Args:
            progress (float): A float between 0 and 1 indicating progress fraction.

        Returns:
            str: An ANSI color code string.
        """
        if self._state == ProgressState.ERROR:
            return Color.RED
        elif progress >= 1:
            return Color.GREEN
        else:
            return Color.BLUE

    def _print_progress(self) -> None:
        """
        Print the current progress bar state to the console, including elapsed time, ETA, and error count.
        """
        elapsed = time.time() - self.start_time
        progress = self.current / self.total if self.total > 0 else 1

        if self.current == 0:
            eta = 0
        else:
            speed = self.current / elapsed
            remaining_items = self.total - self.current
            eta = remaining_items / speed if speed > 0 else 0

        color = self._get_gradient_color(progress)

        # Build animated progress bar
        filled_width = int(self.width * progress)
        if progress < 1:
            # Show animated gradient effect at progress point
            bar = "█" * (filled_width - 1)
            if filled_width > 0:
                bar += "█"  # Pulse effect at progress point
            bar += "░" * (self.width - filled_width)
        else:
            bar = "█" * self.width

        # Format statistics
        percent = f"{progress * 100:.1f}%"
        counts = f"{self.current}/{self.total}"
        speed = self._format_speed()
        elapsed_str = self._format_time(elapsed)
        eta_str = self._format_time(eta)

        # Build error indicator if needed
        if self.error_count > 0:
            error_str = f" {Color.RED}({self.error_count} errors){Color.RESET}"
        else:
            error_str = ""

        # Get spinner frame
        spinner = Spinner.DOTS[self._spinner_idx] if progress < 1 else "✓"

        # Construct status line with color
        status = (
            f"\r{spinner} {Color.BOLD}{self.desc}{Color.RESET} "
            f"{color}|{bar}|{Color.RESET} "
            f"{Color.BOLD}{percent}{Color.RESET} • "
            f"{counts}{error_str} • "
            f"{Color.DIM}{speed} • {elapsed_str}<{eta_str}{Color.RESET}"
        )

        # Clear previous line if needed
        if self._last_len > len(status):
            print(f"\r{' ' * self._last_len}", end="")

        print(status, end="")
        self._last_len = len(status)

        # Print final status on completion
        if self.current >= self.total:
            print()
            if self.error_count > 0:
                error_rate = (self.error_count / self.total) * 100
                print(f"{Color.RED}⚠️  Completed with {self.error_count} errors ({error_rate:.1f}%){Color.RESET}")
                print(f"{Color.DIM}   Set .run(raise_errors=True) to halt and see full error traces{Color.RESET}")
            else:
                print(f"{Color.GREEN}✓ Completed successfully{Color.RESET}")
