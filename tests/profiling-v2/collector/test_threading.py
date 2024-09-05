import glob
import os
import sys
import threading
import uuid

import mock
import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import threading as collector_threading
from tests.profiling.collector import pprof_utils
from tests.profiling.collector.lock_utils import LineNo
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

init_linenos(__file__)


# Helper classes for testing lock collector
class Foo:
    def __init__(self):
        self.foo_lock = threading.Lock()  # !CREATE! foolock_v2

    def foo(self):
        with self.foo_lock:  # !RELEASE! !ACQUIRE! foolock_v2
            pass


class Bar:
    def __init__(self):
        self.foo = Foo()

    def bar(self):
        self.foo.foo()


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
        self.lock_linenos = get_lock_linenos(self.test_name)

        # ddup is available when the native module is compiled
        assert ddup.is_available, "ddup is not available"
        ddup.config(env="test", service=self.test_name, version="my_version", output_filename=self.pprof_prefix)
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
    def test_lock_events_v2(self):
        # The first argument is the recorder.Recorder which is used for the
        # v1 exporter. We don't need it for the v2 exporter.
        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            lock = threading.Lock()  # !CREATE! test_lock_events_v2
            lock.acquire()  # !ACQUIRE! test_lock_events_v2
            lock.release()  # !RELEASE! test_lock_events_v2
        # Calling upload will trigger the exporter to write to a file
        ddup.upload()

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="lock",
                ),
            ],
        )

    def test_lock_acquire_events_class_v2(self):
        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):

            class Foobar(object):
                def lockfunc(self):
                    lock = threading.Lock()  # !CREATE! test_lock_acquire_events_class_v2
                    lock.acquire()  # !ACQUIRE! test_lock_acquire_events_class_v2

            Foobar().lockfunc()

        ddup.upload()

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="lockfunc",
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="lock",
                ),
            ],
        )

    def test_lock_events_tracer_v2(self, tracer):
        resource = str(uuid.uuid4())
        span_type = str(uuid.uuid4())
        with collector_threading.ThreadingLockCollector(
            None,
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
        ):
            lock1 = threading.Lock()  # !CREATE! test_lock_events_tracer_v2_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_v2_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer_v2_2
                lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_v2_2
                lock1.release()  # !RELEASE! test_lock_events_tracer_v2_1
                span_id = t.span_id

            lock2.release()  # !RELEASE! test_lock_events_tracer_v2_2
        ddup.upload()

        linenos1 = get_lock_linenos("test_lock_events_tracer_v2_1")
        linenos2 = get_lock_linenos("test_lock_events_tracer_v2_2")

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
                    trace_resource=resource,
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
                    trace_resource=resource,
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

    def test_lock_events_tracer_late_finish_v2(self, tracer):
        resource = str(uuid.uuid4())
        span_type = str(uuid.uuid4())
        with collector_threading.ThreadingLockCollector(
            None,
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
        ):
            lock1 = threading.Lock()  # !CREATE! test_lock_events_tracer_late_finish_v2_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_v2_1
            span = tracer.start_span("test", span_type=span_type)
            lock2 = threading.Lock()  # !CREATE! test_lock_events_tracer_late_finish_v2_2
            lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_v2_2
            lock1.release()  # !RELEASE! test_lock_events_tracer_late_finish_v2_1
            lock2.release()  # !RELEASE! test_lock_events_tracer_late_finish_v2_2
        span.resource = resource
        span.finish()
        ddup.upload()

        linenos1 = get_lock_linenos("test_lock_events_tracer_late_finish_v2_1")
        linenos2 = get_lock_linenos("test_lock_events_tracer_late_finish_v2_2")

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

    def test_resource_not_collected_v2(self, tracer):
        resource = str(uuid.uuid4())
        span_type = str(uuid.uuid4())
        with collector_threading.ThreadingLockCollector(
            None,
            tracer=tracer,
            capture_pct=100,
            export_libdd_enabled=True,
            endpoint_collection_enabled=False,
        ):
            lock1 = threading.Lock()  # !CREATE! test_resource_not_collected_v2_1
            lock1.acquire()  # !ACQUIRE! test_resource_not_collected_v2_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = threading.Lock()  # !CREATE! test_resource_not_collected_v2_2
                lock2.acquire()  # !ACQUIRE! test_resource_not_collected_v2_2
                lock1.release()  # !RELEASE! test_resource_not_collected_v2_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! test_resource_not_collected_v2_2
        ddup.upload()

        linenos1 = get_lock_linenos("test_resource_not_collected_v2_1")
        linenos2 = get_lock_linenos("test_resource_not_collected_v2_2")

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
                    trace_resource=None,
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
                    trace_resource=None,
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

    @pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
    def test_lock_gevent_tasks_v2(self):
        from gevent import monkey

        monkey.patch_all()

        def play_with_lock():
            lock = threading.Lock()  # !CREATE! test_lock_gevent_tasks_v2
            lock.acquire()  # !ACQUIRE! test_lock_gevent_tasks_v2
            lock.release()  # !RELEASE! test_lock_gevent_tasks_v2

        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            t = threading.Thread(name="foobar", target=play_with_lock)
            t.start()
            t.join()

        ddup.upload()

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="play_with_lock",
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="lock",
                    task_id=t.ident,
                    task_name="foobar",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="play_with_lock",
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="lock",
                    task_id=t.ident,
                    task_name="foobar",
                ),
            ],
        )

    def test_lock_enter_exit_events_v2(self):
        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            th_lock = threading.Lock()  # !CREATE! test_lock_enter_exit_events_v2
            with th_lock:  # !ACQUIRE! !RELEASE! test_lock_enter_exit_events_v2
                pass

        ddup.upload()

        # for enter/exits, we need to update the lock_linenos for versions >= 3.10
        self.lock_linenos = self.lock_linenos._replace(
            release=self.lock_linenos.release + (0 if sys.version_info >= (3, 10) else 1)
        )

        profile = pprof_utils.parse_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="th_lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=self.lock_linenos,
                    lock_name="th_lock",
                ),
            ],
        )

    @pytest.mark.parametrize(
        "inspect_dir_enabled",
        [True, False],
    )
    def test_class_member_lock_v2(self, inspect_dir_enabled):
        with mock.patch("ddtrace.settings.profiling.config.lock.name_inspect_dir", inspect_dir_enabled):
            expected_lock_name = "foo_lock" if inspect_dir_enabled else None

            with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
                foobar = Foo()
                foobar.foo()
                bar = Bar()
                bar.bar()

            ddup.upload()

            linenos = get_lock_linenos("foolock_v2")
            linenos = linenos._replace(release=linenos.release + (0 if sys.version_info >= (3, 10) else 1))
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

    def test_private_lock_v2(self):
        class Foo:
            def __init__(self):
                self.__lock = threading.Lock()  # !CREATE! test_private_lock_v2

            def foo(self):
                with self.__lock:  # !RELEASE! !ACQUIRE! test_private_lock_v2
                    pass

        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            foo = Foo()
            foo.foo()

        ddup.upload()

        linenos = get_lock_linenos("test_private_lock_v2")
        linenos = linenos._replace(release=linenos.release + (0 if sys.version_info >= (3, 10) else 1))

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

    def test_inner_lock_v2(self):
        class Bar:
            def __init__(self):
                self.foo = Foo()

            def bar(self):
                with self.foo.foo_lock:  # !RELEASE! !ACQUIRE! test_inner_lock_v2
                    pass

        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            bar = Bar()
            bar.bar()

        ddup.upload()

        linenos_foo = get_lock_linenos("foolock_v2")
        linenos_bar = get_lock_linenos("test_inner_lock_v2")
        linenos_bar = linenos_bar._replace(
            create=linenos_foo.create,
            release=linenos_bar.release + (0 if sys.version_info >= (3, 10) else 1),
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

    def test_anonymous_lock_v2(self):
        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            with threading.Lock():  # !CREATE! !ACQUIRE! !RELEASE! test_anonymous_lock_v2
                pass
        ddup.upload()

        linenos = get_lock_linenos("test_anonymous_lock_v2")
        linenos = linenos._replace(release=linenos.release + (0 if sys.version_info >= (3, 10) else 1))

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

    def test_global_locks_v2(self):
        with collector_threading.ThreadingLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            from importlib import reload

            from tests.profiling.collector import global_locks

            # Force reload global_locks to ensure that the locks are patched
            # with the collector with export_libdd_enabled. This is special
            # for this test case because the global_locks module is also
            # imported in a test case that doesn't have export_libdd_enabled.
            global_locks = reload(global_locks)

            global_locks.foo()
            global_locks.bar_instance.bar()

        ddup.upload()

        profile = pprof_utils.parse_profile(self.output_filename)
        linenos_foo = LineNo(
            create=4,
            acquire=9,
            release=9 if sys.version_info >= (3, 10) else 10,
        )
        linenos_bar = LineNo(
            create=15,
            acquire=18,
            release=18 if sys.version_info >= (3, 10) else 19,
        )

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
