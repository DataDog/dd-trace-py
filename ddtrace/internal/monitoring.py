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
_TOTAL_SLOTS = 6  # CPython provides slots 0-5
_ALL_SLOTS = tuple(range(_TOTAL_SLOTS))


class MonitoringRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # name -> tool_id for all dd-trace-py consumers
        self._allocations: t.Dict[str, int] = {}  # noqa: UP006

    def acquire(self, name: str) -> t.Optional[int]:  # noqa: UP007
        """Acquire a sys.monitoring tool ID for a dd-trace-py component.

        Hands out any free slot (0-5), skipping slots already allocated by
        this registry or claimed by external tools.

        Args:
            name: Unique name for this consumer. This is also the name
                  passed to ``sys.monitoring.use_tool_id`` and visible via
                  ``sys.monitoring.get_tool()``.

        Returns:
            The allocated tool ID, or ``None`` if no slot is available.
        """
        with self._lock:
            return self._acquire_locked(name)

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

        for slot in _ALL_SLOTS:
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
            _ALL_SLOTS,
        )
        return None

    def release(self, name: str) -> None:
        """
        Release a previously acquired tool ID.

        Calls `sys.monitoring.free_tool_id` and removes the allocation.
        No-op if the name was never acquired
        """
        with self._lock:
            tool_id = self._allocations.pop(name, None)
        if tool_id is None:
            return
        try:
            sys.monitoring.free_tool_id(tool_id)  # type: ignore[attr-defined]
        except Exception:
            log.debug("Failed to free tool_id=%d for '%s'", tool_id, name, exc_info=True)
        log.debug("Released sys.monitoring tool_id=%d for '%s'", tool_id, name)

    def reserve(self, name: str, tool_id: int) -> None:
        """
        Record that a tool ID is managed externally (by a C extension) so
        that `acquire` knows to skip it.

        This doesn't call `sys.monitoring.use_tool_id` — the caller
        (or C extension) is responsible for that. It only updates the
        registry so that `acquire` knows to skip this slot.
        """
        with self._lock:
            self._allocations[name] = tool_id
        log.debug("Reserved sys.monitoring tool_id=%d for '%s'", tool_id, name)

    def unreserve(self, name: str) -> None:
        """
        Remove a reservation made by `reserve`.

        This doesn't call `sys.monitoring.free_tool_id` — the external component is
        responsible for that.
        """
        with self._lock:
            self._allocations.pop(name, None)

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


monitoring_registry = MonitoringRegistry()
