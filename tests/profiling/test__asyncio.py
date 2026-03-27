from __future__ import annotations

import asyncio
import sys
import typing

import pytest

from tests.profiling.collector.test_asyncio_loop_create_task import _LoadedAsyncioModule
from tests.profiling.collector.test_asyncio_loop_create_task import _load_asyncio_module_under_test


@pytest.fixture
def loaded_asyncio_module() -> typing.Iterator[_LoadedAsyncioModule]:
    loaded = _load_asyncio_module_under_test(initialize_asyncio=False)
    try:
        yield loaded
    finally:
        loaded.cleanup()


@pytest.fixture
def restore_event_loop_policy_state() -> typing.Iterator[typing.Any]:
    policy = asyncio.get_event_loop_policy()
    local_state = getattr(policy, "_local", None)
    if local_state is None:
        yield None
        return

    saved_state = dict(local_state.__dict__)
    try:
        yield local_state
    finally:
        local_state.__dict__.clear()
        local_state.__dict__.update(saved_state)


def test_link_existing_loop_to_current_thread_tracks_non_running_loop(
    loaded_asyncio_module: _LoadedAsyncioModule,
    restore_event_loop_policy_state: typing.Any,
) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loaded_asyncio_module.module.ASYNCIO_IMPORTED = True

        loaded_asyncio_module.module.link_existing_loop_to_current_thread()

        assert loaded_asyncio_module.stack.tracked_loops == [
            (
                loaded_asyncio_module.module.ddtrace_threading.current_thread().ident,
                loop,
            )
        ]
        assert len(loaded_asyncio_module.stack.init_calls) == 1
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="test relies on the default policy thread-local loop state",
)
def test_link_existing_loop_to_current_thread_does_not_create_loop(
    loaded_asyncio_module: _LoadedAsyncioModule,
    restore_event_loop_policy_state: typing.Any,
) -> None:
    policy = asyncio.get_event_loop_policy()
    local_state = getattr(policy, "_local", None)
    if local_state is None:
        pytest.skip("event loop policy does not expose thread-local loop state")

    local_state.__dict__.clear()
    loaded_asyncio_module.module.ASYNCIO_IMPORTED = True

    loaded_asyncio_module.module.link_existing_loop_to_current_thread()

    assert local_state.__dict__ == {}
    assert loaded_asyncio_module.stack.tracked_loops == []
    assert loaded_asyncio_module.stack.init_calls == []
