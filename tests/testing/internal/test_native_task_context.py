"""Where the lines of work a test starts on an event loop go, with native line coverage (CPython 3.12+).

Each test of one pytest session starts work in one way, and that work alone runs one marker line of
spawn_helper.py. The marker line must be in the test's record and nowhere else for the ways that carry
the test's native context, and in the Shared record only for the ways that do not. The ways that need
uvloop, trio or anyio are left out when those are not installed.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


if sys.version_info < (3, 12):
    pytest.skip("native line coverage needs sys.monitoring", allow_module_level=True)
pytest.importorskip("ddtrace.internal.coverage._linecov")

_REPO_ROOT = Path(__file__).resolve().parents[3]

_HELPER = """\
import ctypes
import threading


async def asyncio_loop_create_task_ctx():
    return 1  # M:asyncio_loop_create_task_ctx


async def asyncio_create_task_ctx():
    return 1  # M:asyncio_create_task_ctx


async def taskgroup_create_task_ctx():
    return 1  # M:taskgroup_create_task_ctx


async def asyncio_task_direct_ctx():
    return 1  # M:asyncio_task_direct_ctx


def asyncio_call_soon_ctx(fut):
    fut.set_result(1)  # M:asyncio_call_soon_ctx


async def child_task_of_empty_ctx_task():
    return 1  # M:child_task_of_empty_ctx_task


def done_callback_in_empty_ctx_task(fut, out):
    out.append(1)  # M:done_callback_in_empty_ctx_task


async def uvloop_create_task_ctx():
    return 1  # M:uvloop_create_task_ctx


def uvloop_call_soon_ctx(fut):
    fut.set_result(1)  # M:uvloop_call_soon_ctx


async def anyio_start_soon_asyncio():
    return 1  # M:anyio_start_soon_asyncio


async def anyio_start_soon_trio():
    return 1  # M:anyio_start_soon_trio


def anyio_to_thread():
    return 1  # M:anyio_to_thread


async def trio_start_soon():
    return 1  # M:trio_start_soon


async def trio_system_task_ctx(done):
    done.set()  # M:trio_system_task_ctx


async def threadsafe_coroutine():
    return 1  # M:threadsafe_coroutine


async def threadsafe_create_task_ctx():
    return 1  # M:threadsafe_create_task_ctx


async def thread_runs_loop_ctx_task():
    return 1  # M:thread_runs_loop_ctx_task


async def c_ext_via_method_ctx():
    return 1  # M:c_ext_via_method_ctx


async def c_ext_via_task_type_ctx():
    return 1  # M:c_ext_via_task_type_ctx


def start_background_loop(asyncio):
    # An event loop on a thread started at import, before any test: it carries no test's context.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


# What a C extension does: call the loop's create_task attribute, or the task type itself.
_get_attribute = ctypes.pythonapi.PyObject_GetAttrString
_get_attribute.restype = ctypes.py_object
_get_attribute.argtypes = [ctypes.py_object, ctypes.c_char_p]
_call = ctypes.pythonapi.PyObject_Call
_call.restype = ctypes.py_object
_call.argtypes = [ctypes.py_object, ctypes.py_object, ctypes.py_object]


def c_create_task_via_method(loop, coro, context):
    return _call(_get_attribute(loop, b"create_task"), (coro,), {"context": context})


def c_create_task_via_task_type(task_type, loop, coro, context):
    return _call(task_type, (coro,), {"loop": loop, "context": context})
"""

_TESTS = """\
import asyncio
import contextvars
import threading

import pytest

import spawn_helper as helper


BACKGROUND_LOOP = helper.start_background_loop(asyncio)


def test_asyncio_loop_create_task_ctx():
    async def main():
        loop = asyncio.get_running_loop()
        await loop.create_task(helper.asyncio_loop_create_task_ctx(), context=contextvars.Context())

    asyncio.run(main())


def test_asyncio_create_task_ctx():
    async def main():
        await asyncio.create_task(helper.asyncio_create_task_ctx(), context=contextvars.Context())

    asyncio.run(main())


def test_taskgroup_create_task_ctx():
    async def main():
        async with asyncio.TaskGroup() as group:
            group.create_task(helper.taskgroup_create_task_ctx(), context=contextvars.Context())

    asyncio.run(main())


def test_asyncio_task_direct_ctx():
    async def main():
        await asyncio.Task(helper.asyncio_task_direct_ctx(), context=contextvars.Context())

    asyncio.run(main())


def test_asyncio_call_soon_ctx():
    async def main():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        loop.call_soon(helper.asyncio_call_soon_ctx, fut, context=contextvars.Context())
        await fut

    asyncio.run(main())


def test_child_task_of_empty_ctx_task():
    async def parent():
        await asyncio.get_running_loop().create_task(helper.child_task_of_empty_ctx_task())

    async def main():
        await asyncio.get_running_loop().create_task(parent(), context=contextvars.Context())

    asyncio.run(main())


def test_done_callback_in_empty_ctx_task():
    out = []

    async def parent():
        fut = asyncio.get_running_loop().create_future()
        fut.add_done_callback(lambda f: helper.done_callback_in_empty_ctx_task(f, out))
        fut.set_result(None)
        await asyncio.sleep(0)

    async def main():
        await asyncio.get_running_loop().create_task(parent(), context=contextvars.Context())

    asyncio.run(main())
    assert out == [1]


def test_uvloop_create_task_ctx():
    uvloop = pytest.importorskip("uvloop")

    async def main():
        loop = asyncio.get_running_loop()
        await loop.create_task(helper.uvloop_create_task_ctx(), context=contextvars.Context())

    uvloop.run(main())


def test_uvloop_call_soon_ctx():
    uvloop = pytest.importorskip("uvloop")

    async def main():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        loop.call_soon(helper.uvloop_call_soon_ctx, fut, context=contextvars.Context())
        await fut

    uvloop.run(main())


def test_anyio_start_soon_asyncio():
    anyio = pytest.importorskip("anyio")

    async def main():
        async with anyio.create_task_group() as group:
            group.start_soon(helper.anyio_start_soon_asyncio)

    anyio.run(main, backend="asyncio")


def test_anyio_start_soon_trio():
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("trio")

    async def main():
        async with anyio.create_task_group() as group:
            group.start_soon(helper.anyio_start_soon_trio)

    anyio.run(main, backend="trio")


def test_anyio_to_thread():
    anyio = pytest.importorskip("anyio")

    async def main():
        await anyio.to_thread.run_sync(helper.anyio_to_thread)

    anyio.run(main)


def test_trio_start_soon():
    trio = pytest.importorskip("trio")

    async def main():
        async with trio.open_nursery() as nursery:
            nursery.start_soon(helper.trio_start_soon)

    trio.run(main)


def test_trio_system_task_ctx():
    trio = pytest.importorskip("trio")

    async def main():
        done = trio.Event()
        trio.lowlevel.spawn_system_task(helper.trio_system_task_ctx, done, context=contextvars.Context())
        await done.wait()

    trio.run(main)


def test_threadsafe_coroutine():
    # The test's thread hands a coroutine to the loop on the thread started at import.
    asyncio.run_coroutine_threadsafe(helper.threadsafe_coroutine(), BACKGROUND_LOOP).result(10)


def test_threadsafe_create_task_ctx():
    # The test's thread asks that loop to create a task with a new, empty context.
    tasks = []
    created = threading.Event()

    def create():
        tasks.append(BACKGROUND_LOOP.create_task(helper.threadsafe_create_task_ctx(), context=contextvars.Context()))
        created.set()

    BACKGROUND_LOOP.call_soon_threadsafe(create)
    assert created.wait(10)
    asyncio.run_coroutine_threadsafe(asyncio.wait_for(asyncio.shield(tasks[0]), 10), BACKGROUND_LOOP).result(10)


def test_thread_runs_loop_ctx_task():
    # A thread the test starts runs its own loop, which creates a task with a new, empty context.
    async def main():
        loop = asyncio.get_running_loop()
        await loop.create_task(helper.thread_runs_loop_ctx_task(), context=contextvars.Context())

    thread = threading.Thread(target=asyncio.run, args=(main(),))
    thread.start()
    thread.join(10)


def test_c_ext_via_method_ctx():
    async def main():
        loop = asyncio.get_running_loop()
        await helper.c_create_task_via_method(loop, helper.c_ext_via_method_ctx(), contextvars.Context())

    asyncio.run(main())


def test_c_ext_via_task_type_ctx():
    async def main():
        loop = asyncio.get_running_loop()
        coro = helper.c_ext_via_task_type_ctx()
        await helper.c_create_task_via_task_type(asyncio.Task, loop, coro, contextvars.Context())

    asyncio.run(main())
"""

# Each test's record and the Shared record's unowned part, as line numbers per file.
_RECORDING_SCRIPT = f"""
    import json
    import sys
    from unittest.mock import patch

    sys.path.insert(0, {str(_REPO_ROOT)!r})

    import pytest

    from ddtrace.testing.internal.pytest import plugin
    from ddtrace.testing.internal.pytest.plugin import TestOptPlugin
    from ddtrace.testing.internal.tracer_api.coverage import native_record_bitmaps
    from tests.testing.mocks import mock_api_client_settings
    from tests.testing.mocks import setup_standard_mocks

    def lines_of(bitmaps):
        return {{
            path: sorted(i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b))
            for path, bitmap in bitmaps
        }}

    records = {{}}
    shared = {{}}

    def put_test_coverage(self, test_run, coverage_data):
        records[test_run.name] = lines_of(native_record_bitmaps(coverage_data, self.manager.workspace_path))

    get_shared = plugin.get_shared_coverage_bitmaps

    def get_shared_coverage_bitmaps():
        parts = get_shared()
        shared.update(lines_of(parts.get("unowned", [])))
        return parts

    with (
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(coverage_enabled=True),
        ),
        setup_standard_mocks(),
        patch.object(TestOptPlugin, "_put_test_coverage", put_test_coverage),
        patch.object(plugin, "get_shared_coverage_bitmaps", get_shared_coverage_bitmaps),
    ):
        status = pytest.main(["--ddtrace", "-p", "no:cacheprovider", "-p", "no:randomly", "-q", "test_spawns.py"])
    print(json.dumps({{"status": int(status), "records": records, "shared": shared}}))
"""

# The ways the test's native context reaches, and what each needs besides asyncio.
_CREDITED = {
    "asyncio_loop_create_task_ctx": (),
    "asyncio_create_task_ctx": (),
    "taskgroup_create_task_ctx": (),
    "child_task_of_empty_ctx_task": (),
    "done_callback_in_empty_ctx_task": (),
    "uvloop_create_task_ctx": ("uvloop",),
    "anyio_start_soon_asyncio": ("anyio",),
    "anyio_start_soon_trio": ("anyio", "trio"),
    "anyio_to_thread": ("anyio",),
    "trio_start_soon": ("trio",),
    "threadsafe_coroutine": (),
    "threadsafe_create_task_ctx": (),
    "thread_runs_loop_ctx_task": (),
    "c_ext_via_method_ctx": (),
}
# The ways it does not: their lines are in the Shared record.
_SHARED = {
    "asyncio_task_direct_ctx": (),
    "asyncio_call_soon_ctx": (),
    "uvloop_call_soon_ctx": ("uvloop",),
    "trio_system_task_ctx": ("trio",),
    "c_ext_via_task_type_ctx": (),
}


def _available(cases: dict) -> list:
    return [name for name, needs in cases.items() if all(importlib.util.find_spec(m) is not None for m in needs)]


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    root = tmp_path_factory.mktemp("spawns")
    (root / "spawn_helper.py").write_text(_HELPER)
    (root / "test_spawns.py").write_text(_TESTS)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_RECORDING_SCRIPT)],
        env={
            **os.environ,
            "_DD_COVERAGE_NATIVE_LINES": "true",
            "DD_API_KEY": "test-key",
            "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false",
        },
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout.strip().splitlines()[-1])
    assert output["status"] == 0, result.stdout
    markers = {
        line.split("# M:")[1]: number for number, line in enumerate(_HELPER.splitlines(), start=1) if "# M:" in line
    }
    assert set(markers) == set(_CREDITED) | set(_SHARED)

    def helper_lines(files: dict) -> set:
        return set(next((lines for path, lines in files.items() if path.endswith("spawn_helper.py")), []))

    records = {name: helper_lines(files) for name, files in output["records"].items()}
    return markers, records, helper_lines(output["shared"])


@pytest.mark.parametrize("way", _available(_CREDITED))
def test_work_started_this_way_runs_for_the_test(session, way) -> None:
    markers, records, shared = session
    line = markers[way]
    holders = sorted(name for name, lines in records.items() if line in lines)
    assert holders == [f"test_{way}"], (holders, shared)
    assert line not in shared


@pytest.mark.parametrize("way", _available(_SHARED))
def test_work_started_this_way_runs_for_no_test(session, way) -> None:
    markers, records, shared = session
    line = markers[way]
    holders = sorted(name for name, lines in records.items() if line in lines)
    assert holders == [], holders
    assert line in shared
