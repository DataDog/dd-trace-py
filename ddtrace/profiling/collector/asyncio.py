from asyncio.locks import Lock
import typing

from .. import collector
from . import _lock


class _ProfiledAsyncioLock(_lock._ProfiledLock):
    pass


class AsyncioLockCollector(_lock.LockCollector):
    """Record asyncio.Lock usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioLock

    def _start_service(self) -> None:
        """Start collecting lock usage."""
        try:
            import asyncio
        except ImportError as e:
            raise collector.CollectorUnavailable(e)
        self._asyncio_module = asyncio
        return super(AsyncioLockCollector, self)._start_service()

    def _get_patch_target(self) -> typing.Type[Lock]:
        return self._asyncio_module.Lock

    def _set_patch_target(
        self,
        value: typing.Any,
    ) -> None:
        self._asyncio_module.Lock = value  # type: ignore[misc]
