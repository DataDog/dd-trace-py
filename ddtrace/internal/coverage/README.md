# Per-test line coverage: which test a line belongs to

With native line coverage (CPython 3.12+, see `native_lines.py`), the collector credits each line to the
native context held in the `ctx_native` context variable of the code that runs it, while that context is
active. A test's context is active from its setup to its teardown. A line run with no active context goes
to the process's Shared record: initialiser lines (module and class bodies), suite-scope fixture setups,
and everything else ("unowned").

Work a test starts somewhere else runs for the test when that work carries the test's context:

| Work started by a test | How it carries the context |
| --- | --- |
| A thread (`threading.Thread`) | `threading_coverage.py` sets the creator's context in the thread. On Python 3.14+ the target runs in a context of its own, the one the caller passed or else the one `start()` makes, and the creator's context is written into it when it names none |
| A `ThreadPoolExecutor` work item | `submit()` runs each item in the submitter's context |
| An asyncio task created with the default context | asyncio copies the creator's contextvars context |
| A task created with an explicit `context=` through `loop.create_task()`, `asyncio.create_task()` or `TaskGroup.create_task()` (asyncio's loops and uvloop) | The wrapped `create_task()` writes the creator's context into the given context when it names none. Tasks and callbacks started from that task inherit it |
| A child process (subprocess, multiprocessing) | The child's own collector writes a record that the test's window merges |
| A process forked with `os.fork()` or `os.forkpty()`, by the test or by one of its child processes | The forked process drops the lines it inherited and records the lines it runs from the fork on, for the context that ran the fork. It writes the record at exit, through `os._exit()`, and on SIGTERM or SIGHUP unless the signal is ignored |

A context given to a thread or a task gains `ctx_native` and no other variable
(`test_a_given_context_gains_only_the_tracer_variable`).

While child processes are followed, `os._exit` in the test process is a wrapper that writes the
record of a forked process first, so code that checks the identity of `os._exit` sees the wrapper
(`test_exit_wrapper_is_exit_without_a_forked_record`). A forked process blocked in a C call that
signals do not interrupt dies from SIGHUP only when that call returns, because the handler that
writes its record runs between bytecodes (`test_forked_process_blocked_in_a_call_ends_on_sighup`).
The child bootstrap's docstring lists the processes that are not recorded.

The ways below start work in a context that names no running test, and its lines go to the Shared record.
Each is shown by a test in `tests/testing/internal/test_native_task_context.py`.

| Work started by a test | Why its lines are not the test's | Shown by |
| --- | --- | --- |
| `loop.call_soon()` with an explicit `context=`, on asyncio's loops or uvloop | Callback scheduling is not wrapped: asyncio schedules every task step through `call_soon()`, so a wrapper would run on each step | `asyncio_call_soon_ctx`, `uvloop_call_soon_ctx` |
| A task built by calling the task type, `asyncio.Task(coro, context=...)`, from Python or from a C extension | `asyncio.Task` is a C type: its constructor is not wrapped | `asyncio_task_direct_ctx`, `c_ext_via_task_type_ctx` |
| A trio system task, `trio.lowlevel.spawn_system_task(..., context=...)` | trio's task creation is not wrapped | `trio_system_task_ctx` |
| A thread or a task given a context that names a test already, such as a copy saved by an earlier test | A value the caller set is never changed: the work runs for the test the context names, and for no test once that test has ended | `thread_ctx_naming_an_ended_test`, `task_ctx_naming_an_ended_test` |

A C extension that calls the loop's `create_task` attribute goes through the wrapper, like Python code
(`c_ext_via_method_ctx`). An event loop other than asyncio's and uvloop's that defines its own
`create_task()`, without calling asyncio's, is not wrapped. The cases that need uvloop or trio run only
where those are installed.

## Decisions

**No single-context fast path.** While exactly one test's context is active, the line callback
could skip its per-event bookkeeping, and the context would be credited at its next re-arm with
the line starts disabled since the last one. That saves about 20 of the roughly 610 instructions
a line event costs. It adds a mode switch to the hot path: an event owned by another context or
by none, and a context that starts, is suspended or resumes, switch back to the full path first.
The estimate below multiplies the line events of the tests that run with one active context by
20, as percentage points of each suite's instructions with coverage off (CPython 3.12, aarch64).
It is under 0.1 point on every suite.

| Test suite | Events in tests with one active context | Instructions saved | Points |
| --- | --- | --- | --- |
| flask | 176,986 of 176,986 | 3.5M | 0.043 |
| click | 1,185,363 of 1,185,363 | 23.7M | 0.092 |
| httpx | 204,119 of 366,344 | 4.1M | 0.014 |
| django-rest-framework | 379,657 of 398,583 | 7.6M | 0.029 |
| uvicorn | 515,587 of 515,587 | 10.3M | 0.027 |
| fastapi | 3,985,929 of 3,996,321 | 79.7M | 0.022 |
| pydantic | 5,351,324 of 5,385,395 | 107.0M | 0.044 |
| aiohttp | 2,886,461 of 2,886,622 | 57.7M | 0.007 |
