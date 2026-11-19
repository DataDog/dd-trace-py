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
def test_loggers_are_unchanged_without_a_gevent_hub():
    import logging
    import sys

    import ddtrace.auto  # noqa:F401
    from ddtrace.internal import threads
    from ddtrace.internal.logger import get_logger

    assert "gevent.hub" not in sys.modules
    assert type(logging.Logger.manager) is logging.Manager
    assert type(threads.log) is logging.Logger
    assert type(get_logger("ddtrace.test.new")) is logging.Logger

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

    from ddtrace.internal import threads
    from ddtrace.internal.logger import main_hub_logging

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


@pytest.mark.subprocess(env=CLONING, out="['logged in the child']", err=None, timeout=30)
def test_fork_child_handles_thread_records_inline():
    """A fork child might exit with os._exit before its main hub runs, e.g. a multiprocessing worker."""
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging
    import os

    import gevent

    from ddtrace.internal import threads

    messages = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    threads.log.addHandler(ListHandler())
    gevent.sleep(0)

    pid = os.fork()
    if pid == 0:
        thread = threads.Thread(target=lambda: threads.log.warning("logged in the child"), name="test-logger")
        thread.start()
        thread.join()
        os.write(1, repr(messages).encode())
        os._exit(0)

    gevent.monkey.get_original("os", "waitpid")(pid, 0)


@pytest.mark.subprocess(
    env=CLONING,
    out="['logged by an earlier child hook', 'logged by the forking thread']",
    err=None,
    timeout=30,
)
def test_fork_from_another_thread_handles_child_records_inline():
    """The thread that forks becomes the main thread of the child. Records logged before the reset hook runs in the
    child, while another parent thread might have held the queue lock, are not lost either.
    """
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import logging
    import os

    import gevent

    from ddtrace.internal import forksafe
    from ddtrace.internal import threads
    from ddtrace.internal.logger import main_hub_logging

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
            os.write(1, repr(messages).encode())
            os._exit(0)
        waitpid(pid, 0)
        done.release()

    start_new_thread(fork_and_wait, ())
    done.acquire()


@pytest.mark.subprocess(env=CLONING, err=None, timeout=30)
def test_queued_records_are_formatted_when_logged():
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

    def target():
        threads.log.warning("state=%s", state)
        state["phase"] = "mutated"
        try:
            raise ValueError("boom")
        except ValueError:
            threads.log.warning("failed", exc_info=True)
        threads.log.warning("init", extra={"product": "test"})

    thread = threads.Thread(target=target, name="test-logger")
    thread.start()
    thread.join()

    assert drained.wait(timeout=10), records
    assert records[0].getMessage() == "state={'phase': 'logged'}", records[0].getMessage()
    assert records[1].exc_info is None, records[1].exc_info
    assert "ValueError: boom" in records[1].exc_text, records[1].exc_text
    # The rate limiter keys product records on their message, so it gets it as logged.
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
    from ddtrace.internal.logger import MainHubLogging

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

    from ddtrace.internal import threads
    from ddtrace.internal.logger import main_hub_logging

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
