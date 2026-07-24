"""
CPython 3.12 introduced sys.monitoring with 6 tool slots (0-5). Several
dd-trace-py subsystems need their own slot: coverage, error tracking,
exception profiling, and the native call profiler, so this module helps
coordinate tool id allocation

Usage::
    from ddtrace.internal.monitoring import monitoring_registry

    # Acquire any free slot, skipping slots already taken by other
    # dd-trace-py components or external tools:
    tool_id = monitoring_registry.acquire("datadog-coverage")

    # Later, release it:
    monitoring_registry.release("datadog-coverage")

    # For C/C++ extensions that call use_tool_id themselves, reserve the
    # slot so Python-side consumers know it's taken:
    monitoring_registry.reserve("some-c-extension", sys.monitoring.PROFILER_ID)
"""

import sys
import threading
import typing as t

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# CPython exposes 6 tool slots (0-5)
_TOTAL_SLOTS = 6
_ALL_SLOTS = tuple(range(_TOTAL_SLOTS))

_GENERAL_PURPOSE_SLOTS = (4, 3)
_CANONICAL_SLOTS = (0, 2, 5, 1)
_ACQUIRE_ORDER = _GENERAL_PURPOSE_SLOTS + _CANONICAL_SLOTS


def _monitoring_event_flags() -> t.Tuple[int, ...]:  # noqa: UP006
    """Return every individual sys.monitoring event flag (single-bit values).

    Used by ``release`` to unregister any callbacks a previous owner left
    installed. Composite values (e.g. ``NO_EVENTS``) are skipped so only real
    single events are returned. Returns an empty tuple when sys.monitoring is
    unavailable (Python < 3.12).
    """
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is None:
        return ()
    events = monitoring.events
    flags = set()
    for attr in dir(events):
        if attr.startswith("_"):
            continue
        value = getattr(events, attr)
        # Keep single-bit (power-of-two) flags only; skip 0 and composites.
        if isinstance(value, int) and value > 0 and (value & (value - 1)) == 0:
            flags.add(value)
    return tuple(sorted(flags))


class MonitoringRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # name -> tool_id for all dd-trace-py consumers
        self._allocations: t.Dict[str, int] = {}  # noqa: UP006
        # Names of dd-trace-py tools that rely on returning sys.monitoring.DISABLE
        # from their callbacks (e.g. the native call profiler and coverage). A
        # global sys.monitoring.restart_events() would re-arm their DISABLE'd
        # locations, so consumers that call it must consult
        # has_restart_sensitive_tools() first.
        self._disable_tools: t.Set[str] = set()  # noqa: UP006

    def acquire(self, name: str, uses_disable: bool = False) -> t.Optional[int]:  # noqa: UP007
        """Acquire a sys.monitoring tool ID for a dd-trace-py component.

        Hands out a free slot, preferring the general-purpose slots (3, 4) and
        only falling back to the canonical CPython IDs (0, 1, 2, 5) as a last
        resort so third-party tools that hard-code those IDs still fit. Slots
        already allocated by this registry or claimed by external tools are
        skipped.

        Args:
            name: Unique name for this consumer. This is also the name
                  passed to ``sys.monitoring.use_tool_id`` and visible via
                  ``sys.monitoring.get_tool()``.
            uses_disable: Set to True if this consumer returns
                  ``sys.monitoring.DISABLE`` from its callbacks. This lets
                  has_restart_sensitive_tools() report the tool so other
                  components avoid the global restart_events() that would
                  corrupt its state.

        Returns:
            The allocated tool ID, or ``None`` if no slot is available.
        """
        with self._lock:
            tool_id = self._acquire_locked(name)
            if tool_id is not None:
                if uses_disable:
                    self._disable_tools.add(name)
                else:
                    self._disable_tools.discard(name)
            return tool_id

    def _acquire_locked(self, name: str) -> t.Optional[int]:  # noqa: UP007
        # Idempotent: if already allocated and still valid, return it.
        if name in self._allocations:
            tool_id = self._allocations[name]
            try:
                current = sys.monitoring.get_tool(tool_id)  # type: ignore[attr-defined]
            except Exception:
                current = None
            if current == name:
                return tool_id
            # Stale entry; the slot was freed or taken by someone else.
            del self._allocations[name]

        taken_by_us = set(self._allocations.values())

        for slot in _ACQUIRE_ORDER:
            if slot in taken_by_us:
                continue
            try:
                sys.monitoring.use_tool_id(slot, name)  # type: ignore[attr-defined]
            except ValueError:
                # Slot already claimed by an external tool.
                continue
            self._allocations[name] = slot
            log.debug("Acquired sys.monitoring tool_id=%d for '%s'", slot, name)
            return slot

        log.warning(
            "No sys.monitoring tool slot available for '%s' (tried slots %s)",
            name,
            _ACQUIRE_ORDER,
        )
        return None

    def release(self, name: str) -> None:
        """
        Release a previously acquired tool ID.

        Clears the tool's global events and unregisters its callbacks before
        calling `sys.monitoring.free_tool_id`, then removes the allocation.
        This matters because `free_tool_id` only clears the tool name. It
        leaves the event mask and any registered callbacks in place. Without
        this teardown, a slot later handed to a different dd-trace-py component
        could still fire the previous owner's stale callbacks. No-op if the
        name was never acquired.
        """
        with self._lock:
            tool_id = self._allocations.pop(name, None)
            self._disable_tools.discard(name)
        if tool_id is None:
            return
        self._teardown_tool_state(tool_id, name)
        try:
            sys.monitoring.free_tool_id(tool_id)  # type: ignore[attr-defined]
        except Exception:
            log.debug("Failed to free tool_id=%d for '%s'", tool_id, name, exc_info=True)
        log.debug("Released sys.monitoring tool_id=%d for '%s'", tool_id, name)

    @staticmethod
    def _teardown_tool_state(tool_id: int, name: str) -> None:
        """Clear global events and unregister callbacks for a tool id.

        Best-effort: each step is guarded independently so a failure in one
        doesn't prevent the rest (or the subsequent free_tool_id) from running.
        """
        try:
            sys.monitoring.set_events(tool_id, sys.monitoring.events.NO_EVENTS)  # type: ignore[attr-defined]
        except Exception:
            log.debug("Failed to clear events for tool_id=%d ('%s')", tool_id, name, exc_info=True)

        # Unregister any callbacks the previous owner installed so they can't
        # fire under a new owner via lingering (possibly per-code-object) events
        # that this registry can't see.
        for event in _monitoring_event_flags():
            try:
                sys.monitoring.register_callback(tool_id, event, None)  # type: ignore[attr-defined]
            except Exception:
                log.debug(
                    "Failed to unregister callback for tool_id=%d event=%d ('%s')",
                    tool_id,
                    event,
                    name,
                    exc_info=True,
                )

    def reserve(self, name: str, tool_id: int, uses_disable: bool = False) -> None:
        """
        Record that a tool ID is managed externally (by a C extension) so
        that `acquire` knows to skip it.

        This doesn't call `sys.monitoring.use_tool_id` — the caller
        (or C extension) is responsible for that. It only updates the
        registry so that `acquire` knows to skip this slot.

        Set ``uses_disable`` to True when the reserved tool returns
        ``sys.monitoring.DISABLE`` from its callbacks (see acquire()).
        """
        with self._lock:
            self._allocations[name] = tool_id
            if uses_disable:
                self._disable_tools.add(name)
            else:
                self._disable_tools.discard(name)
        log.debug("Reserved sys.monitoring tool_id=%d for '%s'", tool_id, name)

    def unreserve(self, name: str) -> None:
        """
        Remove a reservation made by `reserve`.

        This doesn't call `sys.monitoring.free_tool_id` — the external component is
        responsible for that.
        """
        with self._lock:
            self._allocations.pop(name, None)
            self._disable_tools.discard(name)

    def get_tool_id(self, name: str) -> t.Optional[int]:  # noqa: UP007
        """
        Return the tool ID allocated to name, or None
        """
        with self._lock:
            return self._allocations.get(name)

    def has_external_tools(self, exclude: t.Optional[t.Set[str]] = None) -> bool:  # noqa: UP006,UP007
        """
        Check whether any non-dd-trace-py tool is registered with sys.monitoring.
        """
        with self._lock:
            our_slots = set(self._allocations.values())

        for slot in range(_TOTAL_SLOTS):
            if slot in our_slots:
                continue
            tool_name = sys.monitoring.get_tool(slot)  # type: ignore[attr-defined]
            if tool_name is not None:
                if exclude and tool_name in exclude:
                    continue
                return True
        return False

    def has_restart_sensitive_tools(self, exclude: t.Optional[t.Set[str]] = None) -> bool:  # noqa: UP006,UP007
        """Whether a global sys.monitoring.restart_events() would corrupt another tool.

        Returns True if there is any registered tool — other than those named in
        ``exclude`` — whose disabled-event state a global restart_events() would
        re-arm. That covers two cases:

        - External (non-dd-trace-py) tools, which may use DISABLE internally.
        - dd-trace-py tools that acquired their slot with ``uses_disable=True``
          (the native call profiler), which return sys.monitoring.DISABLE
          and would lose their zero-overhead-after-warmup behaviour if re-armed.

        Callers that rely on the global restart_events() (currently only the
        coverage collector) must check this and avoid the global restart when it
        returns True.
        """
        if self.has_external_tools(exclude=exclude):
            return True
        with self._lock:
            for name in self._disable_tools:
                if exclude and name in exclude:
                    continue
                if name in self._allocations:
                    return True
        return False


monitoring_registry = MonitoringRegistry()
