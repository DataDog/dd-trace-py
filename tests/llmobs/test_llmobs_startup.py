import sys

import pytest


pytestmark = pytest.mark.subprocess(
    timeout=15,
    env={
        "DD_LLMOBS_ENABLED": None,
        "DD_LLMOBS_AGENTLESS_ENABLED": "false",
        "DD_REMOTE_CONFIGURATION_ENABLED": "false",
        "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
        "DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "false",
        "DD_TRACE_STARTUP_LOGS": "false",
        "DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE": "true",
    },
)


def test_remote_enable_after_module_cloning_keeps_main_thread_identity():
    import ddtrace.auto  # noqa: F401  # isort: skip

    import sys

    from ddtrace import config
    from ddtrace.internal import threads
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    assert "threading" not in sys.modules
    failures = []

    def activate():
        try:
            apm_tracing_rc({"llmobs": {"enabled": True, "ml_app_name": "startup"}}, config)
        except BaseException as error:
            failures.append(repr(error))

    worker = threads.Thread(target=activate, name="llmobs-startup-test")
    worker.start()
    worker.join(5)
    assert not failures, failures
    assert LLMObs.enabled
    application_threading = sys.modules.get("threading")
    if application_threading is not None:
        assert application_threading.main_thread().ident == threads._get_ident()
    LLMObs.disable()


def test_remote_periodic_activation_can_exit():
    import ddtrace.auto  # noqa: F401  # isort: skip

    import sys
    import time

    from ddtrace import config
    from ddtrace.internal import threads
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    assert "threading" not in sys.modules
    completed = []

    def activate():
        apm_tracing_rc({"llmobs": {"enabled": True, "ml_app_name": "startup"}}, config)
        completed.append(True)

    worker = threads.PeriodicThread(60, activate, name="llmobs-exit-test", no_wait_at_start=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not completed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert completed
    assert LLMObs.enabled


def test_default_no_startup_agent_discovery():
    import os

    os.environ.pop("DD_LLMOBS_AGENTLESS_ENABLED", None)
    import ddtrace
    from ddtrace.internal import agent

    requests = []

    def info(*args, **kwargs):
        requests.append(1)
        return None

    agent.info = info
    from ddtrace.llmobs import LLMObs

    assert not requests
    assert not isinstance(vars(LLMObs)["_instance"], LLMObs)
    from unittest.mock import patch

    import ddtrace.auto  # noqa: F401

    with patch.object(ddtrace.tracer._span_aggregator.writer, "write"):
        with ddtrace.tracer.trace("normal-request"):
            pass
    assert not isinstance(vars(LLMObs)["_instance"], LLMObs)


def test_default_sdk_api_creates_one_instance_on_demand():
    from unittest.mock import patch

    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._writer import LLMObsExperimentsClient

    assert not isinstance(vars(LLMObs)["_instance"], LLMObs)
    expected = object()
    with patch.object(LLMObsExperimentsClient, "dataset_create", return_value=expected):
        assert LLMObs.create_dataset("example") is expected
        instance = LLMObs._instance
        assert isinstance(instance, LLMObs)
        assert LLMObs.create_dataset("example") is expected
        assert LLMObs._instance is instance
        assert not LLMObs.enabled


def test_default_concurrent_first_access_is_singleton():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from ddtrace.llmobs import LLMObs

    assert not isinstance(vars(LLMObs)["_instance"], LLMObs)
    original = LLMObs.__init__
    constructed = []

    def initialize(self, *args, **kwargs):
        constructed.append(self)
        original(self, *args, **kwargs)

    LLMObs.__init__ = initialize
    barrier = Barrier(8)

    def read(_):
        barrier.wait(timeout=5)
        return LLMObs._instance

    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(read, range(8)))
    assert len(constructed) == 1, len(constructed)
    assert all(instance is instances[0] for instance in instances)


def test_default_explicit_enable_skips_unused_default():
    from ddtrace.llmobs import LLMObs

    original = LLMObs.__init__
    constructed = []

    def initialize(self, *args, **kwargs):
        constructed.append(self)
        original(self, *args, **kwargs)

    LLMObs.__init__ = initialize
    LLMObs.enable(ml_app="check", agentless_enabled=False)
    assert len(constructed) == 1
    assert LLMObs.enabled
    LLMObs.disable()


@pytest.mark.skipif(sys.platform == "win32", reason="requires fork")
@pytest.mark.subprocess(timeout=15, env=pytestmark.kwargs["env"])
def test_default_fork_before_first_access():
    import ddtrace.auto  # noqa: F401  # isort: skip

    import os
    import signal
    import traceback

    from ddtrace.llmobs import LLMObs

    assert not isinstance(vars(LLMObs)["_instance"], LLMObs)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        signal.alarm(8)
        try:
            assert isinstance(LLMObs._instance, LLMObs)
            LLMObs.enable(ml_app="child", agentless_enabled=False)
            LLMObs.disable()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert not isinstance(vars(LLMObs)["_instance"], LLMObs)


def test_default_reader_racing_remote_enable_uses_active_instance():
    import ddtrace.auto  # noqa: F401  # isort: skip

    from threading import Event
    from threading import Thread

    import ddtrace
    from ddtrace.internal import threads
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    entered, release, reading = Event(), Event(), Event()
    constructed, results, errors = [], [], []
    initialize = LLMObs.__init__
    descriptor = type(vars(LLMObs)["_instance"])
    original_get = descriptor.__get__

    def getter(self, obj, owner):
        reading.set()
        return original_get(self, obj, owner)

    descriptor.__get__ = getter

    def initialize_blocked(self, *args, **kwargs):
        constructed.append(self)
        entered.set()
        assert release.wait(timeout=5)
        initialize(self, *args, **kwargs)

    LLMObs.__init__ = initialize_blocked

    def activate():
        try:
            apm_tracing_rc({"llmobs": {"enabled": True, "ml_app_name": "race"}}, ddtrace.config)
        except BaseException as error:
            errors.append(repr(error))

    worker = threads.Thread(target=activate, name="rc-race")
    worker.start()
    assert entered.wait(timeout=5)
    reader = Thread(target=lambda: results.append(LLMObs._instance))
    reader.start()
    assert reading.wait(timeout=5)
    release.set()
    worker.join(5)
    reader.join(5)
    assert not errors, errors
    assert not reader.is_alive()
    assert LLMObs.enabled
    assert len(constructed) == 1
    assert results == [LLMObs._instance]
    LLMObs.disable()


@pytest.mark.skipif(sys.platform == "win32", reason="requires fork")
@pytest.mark.subprocess(timeout=15, env=pytestmark.kwargs["env"])
def test_default_fork_during_first_construction():
    import ddtrace.auto  # noqa: F401  # isort: skip

    import os
    import signal
    from threading import Event
    from threading import Thread
    import traceback

    from ddtrace.llmobs import LLMObs

    entered, release = Event(), Event()
    parent = os.getpid()
    initialize = LLMObs.__init__

    def initialize_blocked(self, *args, **kwargs):
        if os.getpid() == parent:
            entered.set()
            assert release.wait(timeout=5)
        initialize(self, *args, **kwargs)

    LLMObs.__init__ = initialize_blocked
    results = []
    worker = Thread(target=lambda: results.append(LLMObs._instance))
    worker.start()
    assert entered.wait(timeout=5)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:
        signal.alarm(8)
        try:
            assert isinstance(LLMObs._instance, LLMObs)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert results == [LLMObs._instance]
