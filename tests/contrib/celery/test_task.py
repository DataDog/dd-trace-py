import unittest

import celery
import mock
import wrapt

from ddtrace import Pin
from ddtrace.compat import PY2
from ddtrace.contrib.celery.app import patch_app, unpatch_app
from ddtrace.contrib.celery.task import patch_task, unpatch_task
from .utils import patch_task_with_pin

from ..config import REDIS_CONFIG
from ...test_tracer import get_dummy_tracer
from ...util import assert_list_issuperset

EXPECTED_KEYS = ['service', 'resource', 'meta', 'name',
                 'parent_id', 'trace_id', 'span_id',
                 'duration', 'error', 'start',
]

class CeleryTaskTest(unittest.TestCase):
    def assert_items_equal(self, a, b):
        if PY2:
            return self.assertItemsEqual(a, b)
        return self.assertCountEqual(a, b)

    def setUp(self):
        self.broker_url = 'redis://127.0.0.1:{port}/0'.format(port=REDIS_CONFIG['port'])
        self.tracer = get_dummy_tracer()
        self.pin = Pin(service='celery-ignored', tracer=self.tracer)
        patch_app(celery.Celery, pin=self.pin)
        patch_task(celery.Task, pin=self.pin)

    def tearDown(self):
        unpatch_app(celery.Celery)
        unpatch_task(celery.Task)

    def test_patch_task(self):
        """
        When celery.Task is patched
            we patch the __init__, apply, apply_async, and run methods
        """
        # Assert base class methods are patched
        self.assertIsInstance(celery.Task.__init__, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(celery.Task.apply, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(celery.Task.apply_async, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(celery.Task.run, wrapt.BoundFunctionWrapper)

        # Create an instance of a Task
        task = celery.Task()

        # Assert instance methods are patched
        self.assertIsInstance(task.__init__, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(task.apply, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(task.apply_async, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(task.run, wrapt.BoundFunctionWrapper)

    def test_unpatch_task(self):
        """
        When unpatch_task is called on a patched task
            we unpatch the __init__, apply, apply_async, and run methods
        """
        # Assert base class methods are patched
        self.assertIsInstance(celery.Task.__init__, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(celery.Task.apply, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(celery.Task.apply_async, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(celery.Task.run, wrapt.BoundFunctionWrapper)

        # Unpatch the base class
        unpatch_task(celery.Task)

        # Assert the methods are no longer wrapper
        self.assertFalse(isinstance(celery.Task.__init__, wrapt.BoundFunctionWrapper))
        self.assertFalse(isinstance(celery.Task.apply, wrapt.BoundFunctionWrapper))
        self.assertFalse(isinstance(celery.Task.apply_async, wrapt.BoundFunctionWrapper))
        self.assertFalse(isinstance(celery.Task.run, wrapt.BoundFunctionWrapper))

    def test_task_init(self):
        """
        Creating an instance of a patched celery.Task
            will yield a patched instance
        """
        task = celery.Task()

        # Assert instance methods are patched
        self.assertIsInstance(task.__init__, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(task.apply, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(task.apply_async, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(task.run, wrapt.BoundFunctionWrapper)

    def test_task_run(self):
        """
        Calling the run method of a patched task
            calls the original run() method
            creates a span for the call
        """
        # Create an instance of our patched app
        # DEV: No broker url is needed, we this task is run directly
        app = celery.Celery()

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)

        # Call the run method
        patched_task.run()

        # Assert it was called
        task_spy.assert_called_once()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-worker')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.run')
        self.assertEqual(span.error, 0)

        # Assert metadata is correct
        assert_list_issuperset(span.meta.keys(), ['celery.action'])
        self.assertEqual(span.meta['celery.action'], 'run')

    def test_task___call__(self):
        """
        Calling the task directly as a function
            calls the original method
            creates a span for the call
        """
        # Create an instance of our patched app
        # DEV: No broker url is needed, we this task is run directly
        app = celery.Celery()

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)

        # Call the task
        patched_task()

        # Assert it was called
        task_spy.assert_called_once()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-worker')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.run')
        self.assertEqual(span.error, 0)

        # Assert metadata is correct
        assert_list_issuperset(span.meta.keys(), ['celery.action'])
        self.assertEqual(span.meta['celery.action'], 'run')

    def test_task_apply_async(self):
        """
        Calling the apply_async method of a patched task
            calls the original run() method
            creates a span for the call
        """
        # Create an instance of our patched app
        app = celery.Celery()

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)

        # Call the apply method
        patched_task.apply()

        # Assert it was called
        task_spy.assert_called_once()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 2)

        # Assert the first span for calling `apply`
        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.error, 0)

        # Save for later
        parent_span_id = span.span_id

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'state'])
        self.assertEqual(meta['state'], 'SUCCESS')
        self.assertEqual(meta['celery.action'], 'apply')

        # Assert the celery service span for calling `run`
        span = spans[1]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-worker')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.run')
        self.assertEqual(span.parent_id, parent_span_id)
        self.assertEqual(span.error, 0)

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(
            meta.keys(),
            ['celery.delivery_info', 'celery.id', 'celery.action']
        )
        self.assertEqual(meta['celery.action'], 'run')
        self.assertNotEqual(meta['celery.id'], 'None')

        # DEV: Assert as endswith, since PY3 gives us `u'is_eager` and PY2 gives us `'is_eager'`
        self.assertTrue(meta['celery.delivery_info'].endswith('\'is_eager\': True}'))

    def test_task_apply(self):
        """
        Calling the apply method of a patched task
            we do not call the original task method
            creates a span for the call
        """
        # Create an instance of our patched app
        # DEV: We need a broker now since we are publishing a task
        app = celery.Celery('test_task_apply', broker=self.broker_url)

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)
        patched_task.__header__ = mock.Mock()

        # Call the apply method
        patched_task.apply_async()

        # Assert it was called
        task_spy.assert_not_called()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.error, 0)

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'celery.action'])
        self.assertEqual(meta['celery.action'], 'apply_async')

    def test_task_apply_eager(self):
        """
        Calling the apply method of a patched task
            when we are executing tasks eagerly
                we do call the original task method
                creates a span for the call
        """
        # Create an instance of our patched app
        # DEV: We need a broker now since we are publishing a task
        app = celery.Celery('test_task_apply_eager', broker=self.broker_url)
        app.conf['CELERY_ALWAYS_EAGER'] = True

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)
        patched_task.__header__ = mock.Mock()

        # Call the apply method
        patched_task.apply_async()

        # Assert it was called
        task_spy.assert_called_once()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 3)

        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.error, 0)

        # Save for later
        parent_span_id = span.span_id

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'celery.action'])
        self.assertEqual(meta['celery.action'], 'apply_async')

        span = spans[1]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertEqual(span.parent_id, parent_span_id)
        self.assertEqual(span.error, 0)

        # Save for later
        parent_span_id = span.span_id

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'state', 'celery.action'])
        self.assertEqual(meta['state'], 'SUCCESS')
        self.assertEqual(meta['celery.action'], 'apply')

        # The last span emitted
        span = spans[2]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-worker')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.run')
        self.assertEqual(span.parent_id, parent_span_id)
        self.assertEqual(span.error, 0)

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(
            meta.keys(),
            ['celery.delivery_info', 'celery.id', 'celery.action']
        )
        self.assertNotEqual(meta['celery.id'], 'None')
        self.assertEqual(meta['celery.action'], 'run')

        # DEV: Assert as endswith, since PY3 gives us `u'is_eager` and PY2 gives us `'is_eager'`
        self.assertTrue(meta['celery.delivery_info'].endswith('\'is_eager\': True}'))

    def test_task_delay(self):
        """
        Calling the delay method of a patched task
            we do not call the original task method
            creates a span for the call
        """
        # Create an instance of our patched app
        # DEV: We need a broker now since we are publishing a task
        app = celery.Celery('test_task_delay', broker=self.broker_url)

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)
        patched_task.__header__ = mock.Mock()

        # Call the apply method
        patched_task.delay()

        # Assert it was called
        task_spy.assert_not_called()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.error, 0)

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'celery.action'])
        self.assertEqual(meta['celery.action'], 'apply_async')

    def test_task_delay_eager(self):
        """
        Calling the delay method of a patched task
            when we are executing tasks eagerly
                we do call the original task method
                creates a span for the call
        """
        # Create an instance of our patched app
        # DEV: We need a broker now since we are publishing a task
        app = celery.Celery('test_task_delay_eager', broker=self.broker_url)
        app.conf['CELERY_ALWAYS_EAGER'] = True

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = app.task(task_spy)
        patched_task.__header__ = mock.Mock()

        # Call the apply method
        patched_task.delay()

        # Assert it was called
        task_spy.assert_called_once()

        # Assert we created a span
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 3)

        span = spans[0]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.error, 0)

        # Save for later
        parent_span_id = span.span_id

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'celery.action'])
        self.assertEqual(meta['celery.action'], 'apply_async')

        span = spans[1]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-producer')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.apply')
        self.assertEqual(span.parent_id, parent_span_id)
        self.assertEqual(span.error, 0)

        # Save for later
        parent_span_id = span.span_id

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(meta.keys(), ['id', 'state', 'celery.action'])
        self.assertEqual(meta['state'], 'SUCCESS')
        self.assertEqual(meta['celery.action'], 'apply')

        # The last span emitted
        span = spans[2]
        self.assert_items_equal(span.to_dict().keys(), EXPECTED_KEYS)
        self.assertEqual(span.service, 'celery-worker')
        self.assertEqual(span.resource, 'mock.mock.patched_task')
        self.assertEqual(span.name, 'celery.run')
        self.assertEqual(span.parent_id, parent_span_id)
        self.assertEqual(span.error, 0)

        # Assert the metadata is correct
        meta = span.meta
        assert_list_issuperset(
            meta.keys(),
            ['celery.delivery_info', 'celery.id', 'celery.action']
        )
        self.assertNotEqual(meta['celery.id'], 'None')
        self.assertEqual(meta['celery.action'], 'run')

        # DEV: Assert as endswith, since PY3 gives us `u'is_eager` and PY2 gives us `'is_eager'`
        self.assertTrue(meta['celery.delivery_info'].endswith('\'is_eager\': True}'))

    def test_apply_async_previous_style_tasks(self):
        # ensures apply_async is properly patched if Celery 1.0 style tasks
        # are used even in newer versions. This should extend support to
        # previous versions of Celery.
        # Regression test: https://github.com/DataDog/dd-trace-py/pull/449
        app = celery.Celery('test_task_delay_eager', broker=self.broker_url)
        app.conf['CELERY_ALWAYS_EAGER'] = True

        class CelerySuperClass(celery.task.Task):
            abstract = True

            @classmethod
            def apply_async(cls, args=None, kwargs=None, **kwargs_):
                return super(CelerySuperClass, cls).apply_async(args=args, kwargs=kwargs, **kwargs_)

            def run(self, *args, **kwargs):
                if 'stop' in kwargs:
                    # avoid call loop
                    return
                CelerySubClass.apply_async(args=[], kwargs={"stop": True})

        @patch_task_with_pin(pin=self.pin)
        class CelerySubClass(CelerySuperClass):
            pass

        t = CelerySubClass()
        t.run()
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 4)

    def test_celery_shared_task(self):
        @celery.shared_task
        def add(x ,y):
            return x + y

        res = add.run(2, 2)
        self.assertEqual(res, 4)
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, 'celery-worker')
        self.assertEqual(span.resource, 'tests.contrib.celery.test_task.add')
        self.assertEqual(span.name, 'celery.run')
        self.assertIsNone(span.parent_id)
        self.assertEqual(span.error, 0)
