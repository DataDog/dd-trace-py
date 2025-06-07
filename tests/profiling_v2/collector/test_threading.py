import glob
import os
import sys
import threading
import uuid

import mock
import pytest

from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import threading as collector_threading
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

init_linenos(__file__)


# Helper classes for testing lock collector
class Foo:
    def __init__(self):
        self.foo_lock = threading.Lock()  # !CREATE! foolock

    def foo(self):
        with self.foo_lock:  # !RELEASE! !ACQUIRE! foolock
            pass


class Bar:
    def __init__(self):
        self.foo = Foo()

    def bar(self):
        self.foo.foo()


def test_repr():
    test_collector._test_repr(
        collector_threading.ThreadingLockCollector,
        "ThreadingLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=16384, max_events={}), capture_pct=1.0, nframes=64, "
        "endpoint_collection_enabled=True, export_libdd_enabled=True, tracer=None)",
    )


def test_wrapper():
    collector = collector_threading.ThreadingLockCollector()
    with collector:

        class Foobar(object):
            lock_class = threading.Lock

            def __init__(self):
                lock = self.lock_class()
                assert lock.acquire()
                lock.release()

        # Try to access the attribute
        lock = Foobar.lock_class()
        assert lock.acquire()
        lock.release()

        # Try this way too
        Foobar()


def test_patch():
    lock = threading.Lock
    collector = collector_threading.ThreadingLockCollector()
    collector.start()
    assert lock == collector._original
    # wrapt makes this true
    assert lock == threading.Lock
    collector.stop()
    assert lock == threading.Lock
    assert collector._original == threading.Lock


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only works on linux")
@pytest.mark.subprocess(err=None)
# For macOS: Could print 'Error uploading' but okay to ignore since we are checking if native_id is set
def test_user_threads_have_native_id():
    from os import getpid
    from threading import Thread
    from threading import _MainThread
    from threading import current_thread
    from time import sleep

    from ddtrace.profiling import profiler

    # DEV: We used to run this test with ddtrace_run=True passed into the
    # subprocess decorator, but that caused this to be flaky for Python 3.8.x
    # with gevent. When it failed for that specific venv, current_thread()
    # returned a DummyThread instead of a _MainThread.
    p = profiler.Profiler()
    p.start()

    main = current_thread()
    assert isinstance(main, _MainThread)
    # We expect the current thread to have the same ID as the PID
    assert main.native_id == getpid(), (main.native_id, getpid())

    t = Thread(target=lambda: None)
    t.start()

    for _ in range(10):
        try:
            # The TID should be higher than the PID, but not too high
            assert 0 < t.native_id - getpid() < 100, (t.native_id, getpid())
        except AttributeError:
            # The native_id attribute is set by the thread so we might have to
            # wait a bit for it to be set.
            sleep(0.1)
        else:
            break
    else:
        raise AssertionError("Thread.native_id not set")

    t.join()

    p.stop()


@pytest.mark.subprocess(
    env=dict(WRAPT_DISABLE_EXTENSIONS="True", DD_PROFILING_FILE_PATH=__file__),
)
def test_wrapt_disable_extensions():
    import os
    import threading

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import _lock
    from ddtrace.profiling.collector import threading as collector_threading
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    assert ddup.is_available, "ddup is not available"

    # Set up the ddup exporter
    test_name = "test_wrapt_disable_extensions"
    pprof_prefix = "/tmp" + os.sep + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())
    ddup.config()
    ddup.start()

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    # WRAPT_DISABLE_EXTENSIONS is a flag that can be set to disable the C extension
    # for wrapt. It's not set by default in dd-trace-py, but it can be set by
    # users. This test checks that the collector works even if the flag is set.
    assert os.environ.get("WRAPT_DISABLE_EXTENSIONS")
    assert _lock.WRAPT_C_EXT is False

    with collector_threading.ThreadingLockCollector(capture_pct=100):
        th_lock = threading.Lock()  # !CREATE! test_wrapt_disable_extensions
        with th_lock:  # !ACQUIRE! !RELEASE! test_wrapt_disable_extensions
            pass

    ddup.upload(output_filename=pprof_prefix)

    expected_filename = "test_threading.py"

    linenos = get_lock_linenos("test_wrapt_disable_extensions", with_stmt=True)

    profile = pprof_utils.parse_profile(output_filename)
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="<module>",
                filename=expected_filename,
                linenos=linenos,
                lock_name="th_lock",
            )
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="<module>",
                filename=expected_filename,
                linenos=linenos,
                lock_name="th_lock",
            )
        ],
    )


# This test has to be run in a subprocess because it calls gevent.monkey.patch_all()
# which affects the whole process.
@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_FILE_PATH=__file__),
)
def test_lock_gevent_tasks():
    from gevent import monkey

    monkey.patch_all()

    import glob
    import os
    import threading

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import threading as collector_threading
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    assert ddup.is_available, "ddup is not available"

    # Set up the ddup exporter
    test_name = "test_lock_gevent_tasks"
    pprof_prefix = "/tmp" + os.sep + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())
    ddup.config()
    ddup.start()

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    def play_with_lock():
        lock = threading.Lock()  # !CREATE! test_lock_gevent_tasks
        lock.acquire()  # !ACQUIRE! test_lock_gevent_tasks
        lock.release()  # !RELEASE! test_lock_gevent_tasks

    with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
        t = threading.Thread(name="foobar", target=play_with_lock)
        t.start()
        t.join()

    ddup.upload(output_filename=pprof_prefix)

    expected_filename = "test_threading.py"
    linenos = get_lock_linenos(test_name)

    profile = pprof_utils.parse_profile(output_filename)
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="play_with_lock",
                filename=expected_filename,
                linenos=linenos,
                lock_name="lock",
                task_id=t.ident,
                task_name="foobar",
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="play_with_lock",
                filename=expected_filename,
                linenos=linenos,
                lock_name="lock",
                task_id=t.ident,
                task_name="foobar",
            ),
        ],
    )

    for f in glob.glob(pprof_prefix + ".*"):
        try:
            os.remove(f)
        except Exception as e:
            print("Error removing file: {}".format(e))


class TestThreadingLockCollector:
    # setup_method and teardown_method which will be called before and after
    # each test method, respectively, part of pytest api.
    def setup_method(self, method):
        self.test_name = method.__name__
        self.pprof_prefix = "/tmp" + os.sep + self.test_name
        # The output filename will be /tmp/method_name.<pid>.<counter>.
        # The counter number is incremented for each test case, as the tests are
        # all run in a single process and share the same exporter.
        self.output_filename = self.pprof_prefix + "." + str(os.getpid())

        # ddup is available when the native module is compiled
        assert ddup.is_available, "ddup is not available"
        ddup.config()
        ddup.start()

    def teardown_method(self, method):
        # might be unnecessary but this will ensure that the file is removed
        # after each successful test, and when a test fails it's easier to
        # pinpoint and debug.
        for f in glob.glob(self.output_filename + ".*"):
            try:
                os.remove(f)
            except Exception as e:
                print("Error removing file: {}".format(e))
        pass

    # Tests
    def test_lock_events(self):
        # The first argument is the recorder.Recorder which is used for the
        # v1 exporter. We don't need it for the v2 exporter.
        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
            lock = threading.Lock()  # !CREATE! test_lock_events
            lock.acquire()  # !ACQUIRE! test_lock_events
            lock.release()  # !RELEASE! test_lock_events
        # Calling upload will trigger the exporter to write to a file
        ddup.upload(output_filename=self.output_filename)

        profile = pprof_utils.parse_profile(self.output_filename)
        linenos = get_lock_linenos("test_lock_events")
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                ),
            ],
        )

    def test_lock_acquire_events_class(self):
        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):

            class Foobar(object):
                def lockfunc(self):
                    lock = threading.Lock()  # !CREATE! test_lock_acquire_events_class
                    lock.acquire()  # !ACQUIRE! test_lock_acquire_events_class

            Foobar().lockfunc()

        ddup.upload(output_filename=self.output_filename)

        linenos = get_lock_linenos("test_lock_acquire_events_class")

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="lockfunc",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                ),
            ],
        )

    def test_lock_events_tracer(self, tracer):
        tracer._endpoint_call_counter_span_processor.enable()
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with collector_threading.ThreadingLockCollector(
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
        ):
            lock1 = threading.Lock()  # !CREATE! test_lock_events_tracer_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer_2
                lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_2
                lock1.release()  # !RELEASE! test_lock_events_tracer_1
                span_id = t.span_id

            lock2.release()  # !RELEASE! test_lock_events_tracer_2
        ddup.upload(tracer=tracer, output_filename=self.output_filename)

        linenos1 = get_lock_linenos("test_lock_events_tracer_1")
        linenos2 = get_lock_linenos("test_lock_events_tracer_2")

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_lock_events_tracer_non_web(self, tracer):
        tracer._endpoint_call_counter_span_processor.enable()
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.SQL
        with collector_threading.ThreadingLockCollector(
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
        ):
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer_non_web
                lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_non_web
                span_id = t.span_id

            lock2.release()  # !RELEASE! test_lock_events_tracer_non_web
        ddup.upload(tracer=tracer, output_filename=self.output_filename)

        linenos2 = get_lock_linenos("test_lock_events_tracer_non_web")

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                    span_id=span_id,
                    # no trace endpoint for non-web spans
                    trace_type=span_type,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_lock_events_tracer_late_finish(self, tracer):
        tracer._endpoint_call_counter_span_processor.enable()
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with collector_threading.ThreadingLockCollector(
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
        ):
            lock1 = threading.Lock()  # !CREATE! test_lock_events_tracer_late_finish_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_1
            span = tracer.start_span("test", span_type=span_type)
            lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer_late_finish_2
            lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_2
            lock1.release()  # !RELEASE! test_lock_events_tracer_late_finish_1
            lock2.release()  # !RELEASE! test_lock_events_tracer_late_finish_2
        span.resource = resource
        span.finish()
        ddup.upload(tracer=tracer, output_filename=self.output_filename)

        linenos1 = get_lock_linenos("test_lock_events_tracer_late_finish_1")
        linenos2 = get_lock_linenos("test_lock_events_tracer_late_finish_2")

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_resource_not_collected(self, tracer):
        tracer._endpoint_call_counter_span_processor.enable()
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with collector_threading.ThreadingLockCollector(
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
            endpoint_collection_enabled=False,
        ):
            lock1 = threading.Lock()  # !CREATE! test_resource_not_collected_1
            lock1.acquire()  # !ACQUIRE! test_resource_not_collected_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = threading.Lock()  # !CREATE! test_resource_not_collected_2
                lock2.acquire()  # !ACQUIRE! test_resource_not_collected_2
                lock1.release()  # !RELEASE! test_resource_not_collected_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! test_resource_not_collected_2
        ddup.upload(tracer=tracer, output_filename=self.output_filename)

        linenos1 = get_lock_linenos("test_resource_not_collected_1")
        linenos2 = get_lock_linenos("test_resource_not_collected_2")

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                    span_id=span_id,
                    trace_endpoint=None,
                    trace_type=span_type,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                    span_id=span_id,
                    trace_endpoint=None,
                    trace_type=span_type,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_lock_enter_exit_events(self):
        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
            th_lock = threading.Lock()  # !CREATE! test_lock_enter_exit_events
            with th_lock:  # !ACQUIRE! !RELEASE! test_lock_enter_exit_events
                pass

        ddup.upload(output_filename=self.output_filename)

        # for enter/exits, we need to update the lock_linenos for versions >= 3.10
        linenos = get_lock_linenos("test_lock_enter_exit_events", with_stmt=True)

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="th_lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="th_lock",
                ),
            ],
        )

    @pytest.mark.parametrize(
        "inspect_dir_enabled",
        [True, False],
    )
    def test_class_member_lock(self, inspect_dir_enabled):
        with mock.patch("ddtrace.settings.profiling.config.lock.name_inspect_dir", inspect_dir_enabled):
            expected_lock_name = "foo_lock" if inspect_dir_enabled else None

            with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
                foobar = Foo()
                foobar.foo()
                bar = Bar()
                bar.bar()

            ddup.upload(output_filename=self.output_filename)

            linenos = get_lock_linenos("foolock", with_stmt=True)
            profile = pprof_utils.parse_profile(self.output_filename)
            acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
            assert len(acquire_samples) >= 2, "Expected at least 2 lock-acquire samples"
            release_samples = pprof_utils.get_samples_with_value_type(profile, "lock-release")
            assert len(release_samples) >= 2, "Expected at least 2 lock-release samples"

            pprof_utils.assert_lock_events(
                profile,
                expected_acquire_events=[
                    pprof_utils.LockAcquireEvent(
                        caller_name="foo",
                        filename=os.path.basename(__file__),
                        linenos=linenos,
                        lock_name=expected_lock_name,
                    ),
                ],
                expected_release_events=[
                    pprof_utils.LockReleaseEvent(
                        caller_name="foo",
                        filename=os.path.basename(__file__),
                        linenos=linenos,
                        lock_name=expected_lock_name,
                    ),
                ],
            )

    def test_private_lock(self):
        class Foo:
            def __init__(self):
                self.__lock = threading.Lock()  # !CREATE! test_private_lock

            def foo(self):
                with self.__lock:  # !RELEASE! !ACQUIRE! test_private_lock
                    pass

        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
            foo = Foo()
            foo.foo()

        ddup.upload(output_filename=self.output_filename)

        linenos = get_lock_linenos("test_private_lock", with_stmt=True)

        profile = pprof_utils.parse_profile(self.output_filename)

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="foo",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="_Foo__lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="foo",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="_Foo__lock",
                ),
            ],
        )

    def test_inner_lock(self):
        class Bar:
            def __init__(self):
                self.foo = Foo()

            def bar(self):
                with self.foo.foo_lock:  # !RELEASE! !ACQUIRE! test_inner_lock
                    pass

        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
            bar = Bar()
            bar.bar()

        ddup.upload(output_filename=self.output_filename)

        linenos_foo = get_lock_linenos("foolock")
        linenos_bar = get_lock_linenos("test_inner_lock", with_stmt=True)
        linenos_bar = linenos_bar._replace(
            create=linenos_foo.create,
        )

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="bar",
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="bar",
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                ),
            ],
        )

    def test_anonymous_lock(self):
        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
            with threading.Lock():  # !CREATE! !ACQUIRE! !RELEASE! test_anonymous_lock
                pass
        ddup.upload(output_filename=self.output_filename)

        linenos = get_lock_linenos("test_anonymous_lock", with_stmt=True)

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                ),
            ],
        )

    def test_global_locks(self):
        with collector_threading.ThreadingLockCollector(capture_pct=100, export_libdd_enabled=True):
            from tests.profiling.collector import global_locks

            global_locks.foo()
            global_locks.bar_instance.bar()

        ddup.upload(output_filename=self.output_filename)

        profile = pprof_utils.parse_profile(self.output_filename)
        linenos_foo = get_lock_linenos("global_lock", with_stmt=True)
        linenos_bar = get_lock_linenos("bar_lock", with_stmt=True)

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="foo",
                    filename=os.path.basename(global_locks.__file__),
                    linenos=linenos_foo,
                    lock_name="global_lock",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name="bar",
                    filename=os.path.basename(global_locks.__file__),
                    linenos=linenos_bar,
                    lock_name="bar_lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="foo",
                    filename=os.path.basename(global_locks.__file__),
                    linenos=linenos_foo,
                    lock_name="global_lock",
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name="bar",
                    filename=os.path.basename(global_locks.__file__),
                    linenos=linenos_bar,
                    lock_name="bar_lock",
                ),
            ],
        )
