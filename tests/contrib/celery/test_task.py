import celery
import mock
import wrapt

from ddtrace import Pin
from ddtrace.contrib.celery.task import unpatch_task

from .base import CeleryBaseTestCase
from ...util import assert_list_issuperset


EXPECTED_KEYS = ['service', 'resource', 'meta', 'name',
                 'parent_id', 'trace_id', 'span_id',
                 'duration', 'error', 'start',
]


class CeleryTaskTest(CeleryBaseTestCase):
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
        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)

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
        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)

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
        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)

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
        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)
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
        self.app.conf['CELERY_ALWAYS_EAGER'] = True

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)
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
        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)
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
        self.app.conf['CELERY_ALWAYS_EAGER'] = True

        # Create our test task
        task_spy = mock.Mock(__name__='patched_task')
        patched_task = self.app.task(task_spy)
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

    def test_celery_shared_task(self):
        # Ensure Django Shared Task are supported
        @celery.shared_task
        def add(x ,y):
            return x + y

        # TODO[manu]: this should not happen. We're not propagating the `Pin`
        # from the main app and so it's difficult to change globally (or per `Task`)
        # our tracing configurations. After solving the Pin propagation, remove
        # this `Pin.override`.
        # Probably related to: https://github.com/DataDog/dd-trace-py/issues/510
        Pin.override(add, tracer=self.tracer)

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
