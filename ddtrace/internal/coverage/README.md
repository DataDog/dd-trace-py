# Per-test line coverage: which test a line belongs to

With native line coverage (CPython 3.12+, see `native_lines.py`), the collector credits each line to the
native context held in the `ctx_native` context variable of the code that runs it, while that context is
active. A test's context is active from its setup to its teardown. A line run with no active context goes
to the process's Shared record: initialiser lines (module and class bodies), suite-scope fixture setups,
and everything else ("unowned").

Work a test starts somewhere else runs for the test when that work carries the test's context:

| Work started by a test | How it carries the context |
| --- | --- |
| A thread (`threading.Thread`) | `threading_coverage.py` sets the creator's context in the thread, and on Python 3.14+ in the context `start()` makes for it, unless the caller passed one |
| A `ThreadPoolExecutor` work item | `submit()` runs each item in the submitter's context |
| An asyncio task created with the default context | asyncio copies the creator's contextvars context |
| A task created with an explicit `context=` through `loop.create_task()`, `asyncio.create_task()` or `TaskGroup.create_task()` (asyncio's loops and uvloop) | The wrapped `create_task()` writes the creator's context into the given context when it names none. Tasks and callbacks started from that task inherit it |
| A child process (subprocess, multiprocessing) | The child's own collector writes a record that the test's window merges |

The ways below start work in a context that names no test, and its lines go to the Shared record. Each is
shown by a test in `tests/testing/internal/test_native_task_context.py`.

| Work started by a test | Why its lines are not the test's | Shown by |
| --- | --- | --- |
| `loop.call_soon()` with an explicit `context=`, on asyncio's loops or uvloop | Callback scheduling is not wrapped: asyncio schedules every task step through `call_soon()`, so a wrapper would run on each step | `asyncio_call_soon_ctx`, `uvloop_call_soon_ctx` |
| A task built by calling the task type, `asyncio.Task(coro, context=...)`, from Python or from a C extension | `asyncio.Task` is a C type: its constructor is not wrapped | `asyncio_task_direct_ctx`, `c_ext_via_task_type_ctx` |
| A trio system task, `trio.lowlevel.spawn_system_task(..., context=...)` | trio's task creation is not wrapped | `trio_system_task_ctx` |

A C extension that calls the loop's `create_task` attribute goes through the wrapper, like Python code
(`c_ext_via_method_ctx`). An event loop other than asyncio's and uvloop's that defines its own
`create_task()`, without calling asyncio's, is not wrapped. The cases that need uvloop or trio run only
where those are installed.
