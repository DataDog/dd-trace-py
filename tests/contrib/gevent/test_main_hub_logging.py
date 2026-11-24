"""Records that ddtrace threads log under gevent are handled on the main hub (see MainHubLogging).

Each test runs in a subprocess with module cloning enabled, and with a timeout so that a deadlock fails the test.
"""

import pytest


# Records logged from the same line are not rate limited.
CLONING = dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true", DD_TRACE_LOGGING_RATE="0")


@pytest.mark.subprocess(env=CLONING, out=lambda out: "logged on shutdown" in out, err=None, timeout=30)
def test_records_logged_while_threads_join_at_exit_do_not_wait_for_handler_lock():
    """At exit, the main thread joins the ddtrace threads, which can log from on_shutdown while a greenlet holds a
    handler lock. Records are queued until the threads have been joined, then emitted.
    """
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging
    import sys

    import gevent

    from ddtrace.internal import threads

    class StdoutHandler(logging.Handler):
        def emit(self, record):
            sys.stdout.write(record.getMessage() + "\n")
            sys.stdout.flush()

    handler = StdoutHandler()
    threads.log.addHandler(handler)
    logged = []

    def on_shutdown():
        threads.log.warning("logged on shutdown")
        logged.append(True)

    def hold_handler_lock():
        # The main hub runs this greenlet whenever the main thread yields, up until the exit flush.
        with handler.lock:
            while not logged:
                gevent.sleep(0.01)

    gevent.spawn(hold_handler_lock)
    gevent.sleep(0)

    threads.PeriodicThread(60, lambda: None, name="test-exit-logger", on_shutdown=on_shutdown).start()


@pytest.mark.subprocess(env=CLONING, out="logged before exit\n", err=None, timeout=30)
def test_queued_records_are_emitted_at_exit():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging
    import sys

    import gevent

    from ddtrace.internal import threads

    class StdoutHandler(logging.Handler):
        def emit(self, record):
            sys.stdout.write(record.getMessage() + "\n")

    threads.log.addHandler(StdoutHandler())
    gevent.sleep(0)

    # The record is queued, and the main hub might not run again before exit.
    thread = threads.Thread(target=lambda: threads.log.warning("logged before exit"), name="test-logger")
    thread.start()
    thread.join()


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_records_are_handled_inline_when_threading_is_not_patched():
    """With gevent used but threading not patched, the logging locks are native, and the hub might not run for a
    while, so the records are handled on the thread that logs them.
    """
    import logging

    import gevent

    import ddtrace.auto  # noqa:F401
    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)  # Creates the main hub.

    thread = threads.Thread(target=lambda: threads.log.warning("logged from a ddtrace thread"), name="test-logger")
    thread.start()
    thread.join()

    # The hub has not run since the thread started.
    assert messages == ["logged from a ddtrace thread"], messages


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_records_are_handled_inline_without_a_gevent_hub():
    import logging
    import sys

    import ddtrace.auto  # noqa:F401
    from ddtrace.internal import threads

    assert "gevent.hub" not in sys.modules

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    threads.log.addHandler(ListHandler())
    thread = threads.Thread(target=lambda: threads.log.warning("logged from a ddtrace thread"), name="test-logger")
    thread.start()
    thread.join()

    assert messages == ["logged from a ddtrace thread"], messages


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_records_are_handled_inline_when_the_queue_is_full():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.contrib.internal.gevent.logger import main_hub_logging
    from ddtrace.internal import threads

    main_hub_logging.max_records = 2
    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            # The last record is the first one handled, on the logging thread, so this runs on the main one.
            if len(messages) == 3:
                drained.set()

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)

    def target():
        for message in ("first", "second", "third"):
            threads.log.warning(message)

    thread = threads.Thread(target=target, name="test-logger")
    thread.start()
    thread.join()

    assert messages == ["third"], messages
    assert drained.wait(timeout=10), messages
    assert messages == ["third", "first", "second"], messages


@pytest.mark.subprocess(
    env=CLONING,
    out="['logged by an earlier child hook', 'logged by the forking thread', 'logged in the child']",
    err=None,
    timeout=30,
)
def test_fork_from_another_thread_handles_child_records_inline():
    """The thread that forks becomes the main thread of the child. Records logged before the reset hook runs in the
    child, while another parent thread might have held the queue lock, are not lost either. Nor are those of the
    ddtrace threads of the child, which might exit with os._exit before its hub runs.
    """
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging
    import os

    import gevent

    from ddtrace.contrib.internal.gevent.logger import main_hub_logging
    from ddtrace.internal import forksafe
    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)

    def early_child_hook():
        # As if another thread of the parent had held the lock when forking.
        main_hub_logging._lock.acquire()
        threads.log.warning("logged by an earlier child hook")

    forksafe._registry.insert(0, early_child_hook)

    start_new_thread, allocate_lock = gevent.monkey.get_original("_thread", ["start_new_thread", "allocate_lock"])
    fork, waitpid = gevent.monkey.get_original("os", ["fork", "waitpid"])
    done = allocate_lock()
    done.acquire()

    def fork_and_wait():
        pid = fork()
        if pid == 0:
            threads.log.warning("logged by the forking thread")
            thread = threads.Thread(target=lambda: threads.log.warning("logged in the child"), name="test-logger")
            thread.start()
            thread.join()
            os.write(1, repr(messages).encode())
            os._exit(0)
        waitpid(pid, 0)
        done.release()

    start_new_thread(fork_and_wait, ())
    done.acquire()


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_queued_records_keep_their_shape_and_the_arguments_as_logged():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.internal import threads

    records = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)
            if len(records) == 3:
                drained.set()

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)
    state = {"phase": "logged"}
    failure = []

    def target():
        threads.log.warning("state=%s", state)
        state["phase"] = "mutated"
        try:
            raise ValueError("boom")
        except ValueError as e:
            failure.append(e)
            threads.log.warning("failed", exc_info=True)
        threads.log.warning("init", extra={"product": "test"})

    thread = threads.Thread(target=target, name="test-logger")
    thread.start()
    thread.join()

    assert drained.wait(timeout=10), records
    # The message stays the constant template, and the arguments are the ones logged, even when mutated since.
    assert records[0].msg == "state=%s" and records[0].args == {"phase": "logged"}, records[0].__dict__
    assert records[0].getMessage() == "state={'phase': 'logged'}", records[0].getMessage()
    # The exception stays on the record, for the handlers to format.
    assert records[1].msg == "failed" and records[1].exc_info[1] is failure[0], records[1].exc_info
    assert records[2].getMessage() == "test::init", records[2].getMessage()


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_ddtrace_loggers_from_getlogger_do_not_wait_for_greenlet_holding_handler_lock():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.internal import threads

    class CustomLogger(logging.Logger):
        pass

    before_hub = logging.getLogger("ddtrace.test.before_hub")
    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            if len(messages) == 3:
                drained.set()

    handler = ListHandler()
    logging.getLogger("ddtrace").addHandler(handler)
    gevent.sleep(0)  # Creates the main hub.

    after_hub = logging.getLogger("ddtrace.test.after_hub")
    logging.setLoggerClass(CustomLogger)
    custom = logging.getLogger("ddtrace.test.custom")
    logging.setLoggerClass(logging.Logger)
    assert isinstance(custom, CustomLogger)

    release = gevent.event.Event()

    def hold_handler_lock():
        with handler.lock:
            release.wait()

    holder = gevent.spawn(hold_handler_lock)
    gevent.sleep(0)

    def target():
        for logger in (before_hub, after_hub, custom):
            logger.warning(logger.name)

    thread = threads.Thread(target=target, name="test-logger")
    thread.start()
    thread.join()

    release.set()
    holder.join()
    assert drained.wait(timeout=10), messages
    assert messages == ["ddtrace.test.before_hub", "ddtrace.test.after_hub", "ddtrace.test.custom"], messages


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_main_hub_created_before_ddtrace_is_used():
    import gevent

    gevent.sleep(0)  # Creates the main hub before ddtrace is loaded.

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent.event

    from ddtrace.internal import threads

    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            drained.set()

    handler = ListHandler()
    threads.log.addHandler(handler)
    release = gevent.event.Event()

    def hold_handler_lock():
        with handler.lock:
            release.wait()

    holder = gevent.spawn(hold_handler_lock)
    gevent.sleep(0)

    thread = threads.Thread(target=lambda: threads.log.warning("logged from a ddtrace thread"), name="test-logger")
    thread.start()
    thread.join()

    release.set()
    holder.join()
    assert drained.wait(timeout=10), messages
    assert messages == ["logged from a ddtrace thread"], messages


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_failing_filter_does_not_hold_back_queued_records():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.internal import threads

    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            drained.set()

    def fail_on_first(record):
        if record.getMessage() == "first":
            raise RuntimeError("filter failed")
        return True

    threads.log.addHandler(ListHandler())
    threads.log.addFilter(fail_on_first)
    gevent.sleep(0)

    def target():
        threads.log.warning("first")
        threads.log.warning("second")

    thread = threads.Thread(target=target, name="test-logger")
    thread.start()
    thread.join()

    assert drained.wait(timeout=10), messages
    assert messages == ["second"], messages


@pytest.mark.subprocess(timeout=30)
def test_set_hub_sets_spawn_raw_before_the_hub():
    """A thread that sees the hub must also see the function to spawn the drainer with."""
    from ddtrace.contrib.internal.gevent.logger import MainHubLogging

    assigned = []

    class RecordingMainHubLogging(MainHubLogging):
        def __setattr__(self, name, value):
            assigned.append(name)
            super().__setattr__(name, value)

    main_hub_logging = RecordingMainHubLogging()
    del assigned[:]
    main_hub_logging.set_hub(object(), lambda *args: None)

    assert [name for name in assigned if name in ("_hub", "_spawn_raw")] == ["_spawn_raw", "_hub"], assigned


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_record_queued_during_exit_flush_is_emitted():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent

    from ddtrace.contrib.internal.gevent.logger import main_hub_logging
    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)

    allocate_lock = gevent.monkey.get_original("_thread", "allocate_lock")
    putting = allocate_lock()
    putting.acquire()
    flushed = allocate_lock()
    flushed.acquire()
    records = main_hub_logging._records

    class PausingQueue:
        """Delays the put until the exit flush has completed, or for a second if the flush waits for the put."""

        def put(self, item):
            putting.release()
            flushed.acquire(timeout=1)
            records.put(item)

        def __getattr__(self, name):
            return getattr(records, name)

    main_hub_logging._records = PausingQueue()
    thread = threads.Thread(target=lambda: threads.log.warning("logged during the exit flush"), name="test-logger")
    thread.start()
    putting.acquire()
    main_hub_logging.flush_at_exit()
    flushed.release()
    thread.join()

    assert messages == ["logged during the exit flush"], messages


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_records_from_several_threads_are_handled_in_logging_order():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.internal import threads

    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            if len(messages) == 4:
                drained.set()

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)

    # Each thread waits for its turn, so that the records are logged in the order of the turns.
    allocate_lock = gevent.monkey.get_original("_thread", "allocate_lock")
    turns = [allocate_lock() for _ in range(4)]
    for turn in turns[1:]:
        turn.acquire()

    def target(name, mine):
        for i in mine:
            turns[i].acquire()
            threads.log.warning("%s%d", name, i)
            if i + 1 < len(turns):
                turns[i + 1].release()

    first = threads.Thread(target=lambda: target("a", (0, 2)), name="test-logger-a")
    second = threads.Thread(target=lambda: target("b", (1, 3)), name="test-logger-b")
    first.start()
    second.start()
    first.join()
    second.join()

    assert drained.wait(timeout=10), messages
    assert messages == ["a0", "b1", "a2", "b3"], messages


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_level_changes_apply_to_records_from_ddtrace_threads():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.internal import threads
    from ddtrace.internal.logger import get_logger

    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            drained.set()

    gevent.sleep(0)
    log = get_logger("ddtrace.test.level")
    log.addHandler(ListHandler())
    log.setLevel(logging.WARNING)
    log.debug("caches that debug is disabled")

    def log_debug(message):
        thread = threads.Thread(target=lambda: log.debug(message), name="test-logger")
        thread.start()
        thread.join()

    log_debug("logged at warning level")
    log.setLevel(logging.DEBUG)
    log_debug("logged at debug level")

    assert drained.wait(timeout=10), messages
    assert messages == ["logged at debug level"], messages


@pytest.mark.subprocess(
    env=CLONING,
    parametrize={"FORK": ["os.fork", "fork_and_watch"]},
    out="['logged in the child']",
    err=None,
    timeout=30,
)
def test_fork_child_hands_thread_records_to_its_hub_once_it_runs():
    """gevent reinitializes the loop of the main hub in the child, which keeps the same hub."""
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import faulthandler
    import logging
    import os

    import gevent
    import gevent.event
    import gevent.os

    from ddtrace.internal import threads

    messages = []
    drained = gevent.event.Event()

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
            drained.set()

    handler = ListHandler()
    threads.log.addHandler(handler)
    gevent.sleep(0)

    pid = os.fork() if os.environ["FORK"] == "os.fork" else gevent.os.fork_and_watch()
    if pid == 0:
        faulthandler.dump_traceback_later(20, exit=True)
        gevent.sleep(0)  # The hub runs in the child.
        release = gevent.event.Event()

        def hold_handler_lock():
            with handler.lock:
                release.wait()

        holder = gevent.spawn(hold_handler_lock)
        gevent.sleep(0)
        thread = threads.Thread(target=lambda: threads.log.warning("logged in the child"), name="test-logger")
        thread.start()
        thread.join()
        release.set()
        holder.join()
        drained.wait(timeout=10)
        os.write(1, repr(messages).encode())
        os._exit(0)

    gevent.monkey.get_original("os", "waitpid")(pid, 0)


@pytest.mark.subprocess(env=CLONING, out="logged in the worker\n", err=None, timeout=30)
def test_multiprocessing_fork_child_emits_queued_records_before_it_exits():
    """A multiprocessing child exits with os._exit, which skips atexit."""
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import faulthandler
    import logging
    import multiprocessing
    import os

    import gevent
    import gevent.event

    from ddtrace.internal import threads

    class WriteHandler(logging.Handler):
        def emit(self, record):
            os.write(1, (record.getMessage() + "\n").encode())

    handler = WriteHandler()
    threads.log.addHandler(handler)
    gevent.sleep(0)

    def worker():
        faulthandler.dump_traceback_later(20, exit=True)
        gevent.sleep(0)  # The hub runs in the child.
        release = gevent.event.Event()

        def hold_handler_lock():
            with handler.lock:
                release.wait()

        gevent.spawn(hold_handler_lock)
        gevent.sleep(0)
        thread = threads.Thread(target=lambda: threads.log.warning("logged in the worker"), name="test-logger")
        thread.start()
        thread.join()
        release.set()
        # The worker returns without running the hub again.

    process = multiprocessing.get_context("fork").Process(target=worker)
    process.start()
    process.join()
    assert process.exitcode == 0, process.exitcode


@pytest.mark.subprocess(
    env=CLONING, parametrize={"HUB_BEFORE_FORK": ["0", "1"]}, out="['logged in the child']", err=None, timeout=30
)
def test_hub_of_the_thread_that_forked_is_the_main_hub_of_the_child():
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import faulthandler
    import logging
    import os

    import gevent
    import gevent.event
    import gevent.os

    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    handler = ListHandler()
    threads.log.addHandler(handler)
    gevent.sleep(0)

    start_new_thread, allocate_lock = gevent.monkey.get_original("_thread", ["start_new_thread", "allocate_lock"])
    waitpid = gevent.monkey.get_original("os", "waitpid")
    done = allocate_lock()
    done.acquire()

    def fork_and_wait():
        if os.environ["HUB_BEFORE_FORK"] == "1":
            gevent.sleep(0)  # Creates the hub of this thread before the fork.
        # The patched os.fork watches the child, which only works on the main thread.
        pid = gevent.os.fork_gevent()
        if pid == 0:
            faulthandler.dump_traceback_later(20, exit=True)
            drained = gevent.event.Event()
            handler.emit = lambda record: (messages.append(record.getMessage()), drained.set())
            gevent.sleep(0)  # The hub of the forking thread runs as the main hub of the child.
            release = gevent.event.Event()

            def hold_handler_lock():
                with handler.lock:
                    release.wait()

            holder = gevent.spawn(hold_handler_lock)
            gevent.sleep(0)
            thread = threads.Thread(target=lambda: threads.log.warning("logged in the child"), name="test-logger")
            thread.start()
            thread.join()
            release.set()
            holder.join()
            drained.wait(timeout=10)
            os.write(1, repr(messages).encode())
            os._exit(0)
        waitpid(pid, 0)
        done.release()

    start_new_thread(fork_and_wait, ())
    done.acquire()


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_ddtrace_loggers_are_patched_per_instance_and_unpatch_restores_them():
    """Nothing changes the class of a logger, and only ddtrace loggers are patched."""
    import logging

    early = logging.getLogger("ddtrace.test.early")
    get_logger = logging.Manager.getLogger

    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    from ddtrace.contrib.internal.gevent.patch import patch
    from ddtrace.contrib.internal.gevent.patch import unpatch
    from ddtrace.internal.logger import get_logger as get_ddtrace_logger

    patch()

    class CustomLogger(logging.Logger):
        pass

    child = logging.getLogger("ddtrace.test.placeholder.child")  # Its parent is a placeholder until it is created
    loggers = [get_ddtrace_logger("ddtrace.test.get_logger"), logging.getLogger("ddtrace.test.getLogger"), child]
    loggers.append(logging.getLogger("ddtrace.test.placeholder"))
    logging.setLoggerClass(CustomLogger)
    custom = logging.getLogger("ddtrace.test.custom")
    logging.setLoggerClass(logging.Logger)
    loggers.append(custom)
    other = logging.getLogger("test.other")

    assert logging.Manager.getLogger is not get_logger
    # A logger created before ddtrace patched gevent is patched too.
    assert all("handle" in vars(logger) for logger in loggers + [early])
    assert type(early) is logging.Logger and type(custom) is CustomLogger
    assert "handle" not in vars(other) and "isEnabledFor" not in vars(other)
    assert child.parent is loggers[3]

    unpatch()

    assert logging.Manager.getLogger is get_logger
    assert not any(name in vars(logger) for logger in loggers + [early] for name in ("handle", "isEnabledFor"))


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"), err=None, timeout=30)
def test_thread_logging_does_not_wait_for_greenlet_holding_handler_lock():
    """A ddtrace thread that logs while a greenlet holds a handler lock can be joined from the main thread.

    Once gevent patches threading, handler locks are gevent locks. The thread must not wait for one, because the
    main thread's hub, which would let the greenlet release it, does not run during the join.
    """
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging

    import gevent
    import gevent.event

    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    handler = ListHandler()
    logging.getLogger("ddtrace").addHandler(handler)

    release = gevent.event.Event()

    def hold_handler_lock():
        with handler.lock:
            release.wait()

    holder = gevent.spawn(hold_handler_lock)
    gevent.sleep(0)

    thread = threads.Thread(target=lambda: threads.log.warning("logged from a ddtrace thread"), name="test-logger")
    thread.start()
    thread.join()

    release.set()
    holder.join()
    for _ in range(1000):
        if messages:
            break
        gevent.sleep(0.01)

    assert messages == ["logged from a ddtrace thread"], messages


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"), err=None, timeout=30)
def test_fork_while_restart_timer_logs_and_greenlet_holds_handler_lock():
    """A fork does not deadlock with the restart timer logging under _forking_lock while a greenlet holds a log lock.

    Regression test for the startup deadlock of two forks within the restart timer's window.
    """
    import faulthandler

    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging
    import os

    import gevent

    # Dump the stacks of a deadlock before the subprocess timeout kills it.
    faulthandler.dump_traceback_later(20, exit=True)
    # Wait natively: the patched waitpid depends on the hub handling SIGCHLD, which varies the timing under load.
    waitpid = gevent.monkey.get_original("os", "waitpid")

    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    handler = ListHandler()
    logging.getLogger("ddtrace").addHandler(handler)
    threads.log.setLevel(logging.DEBUG)

    def fork():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        waitpid(pid, 0)

    worker = threads.PeriodicThread(0.01, lambda: None, name="test-worker")
    worker.start()

    # The timer restarts the worker 100ms after this fork, logging under _forking_lock.
    fork()

    def hold_handler_lock():
        with handler.lock:
            gevent.sleep(1)

    holder = gevent.spawn(hold_handler_lock)
    gevent.sleep(threads.ThreadRestartTimer.__timeout__ / 1e9 * 3)
    # This fork waits for _forking_lock in its before-fork hook. The main thread then logs, waiting for the holder.
    fork()

    holder.join()
    for _ in range(1000):
        if "Restarting thread test-worker after fork" in messages:
            break
        gevent.sleep(0.01)

    assert "Restarting thread test-worker after fork" in messages, messages
    worker.stop()
    worker.join()
    faulthandler.cancel_dump_traceback_later()
