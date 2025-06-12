import logging
import time

import mock
import pytest

import ddtrace.internal.logger
from ddtrace.internal.logger import LoggingBucket
from ddtrace.internal.logger import get_logger
from tests.utils import BaseTestCase


ALL_LEVEL_NAMES = ("debug", "info", "warning", "error", "exception", "critical", "fatal")


class LoggerTestCase(BaseTestCase):
    def setUp(self):
        super(LoggerTestCase, self).setUp()

        self.manager = logging.root.manager

        # Reset to default values
        ddtrace.internal.logger._buckets.clear()
        ddtrace.internal.logger._rate_limit = 60

    def tearDown(self):
        # Weeee, forget all existing loggers
        logging.Logger.manager.loggerDict.clear()
        self.assertEqual(logging.Logger.manager.loggerDict, dict())

        self.manager = None

        # Reset to default values
        ddtrace.internal.logger._buckets.clear()
        ddtrace.internal.logger._rate_limit = 60

        super(LoggerTestCase, self).tearDown()

    def _make_record(
        self,
        logger,
        msg="test",
        args=(),
        level=logging.INFO,
        fn="module.py",
        lno=5,
        exc_info=(None, None, None),
        func=None,
        extra=None,
    ):
        return logger.makeRecord(logger.name, level, fn, lno, msg, args, exc_info, func, extra)

    def test_get_logger(self):
        """
        When using `get_logger` to get a logger
            When the logger does not exist
                We create a new logging.Logger
            When the logger exists
                We return the expected logger
            When a different logger is requested
                We return a new logging.Logger
            When a Placeholder exists
                We return logging.Logger
        """
        assert self.manager is not None

        # Assert the logger doesn't already exist
        self.assertNotIn("test.logger", self.manager.loggerDict)

        # Fetch a new logger
        log = get_logger("test.logger")
        assert ddtrace.internal.logger.log_filter in log.filters
        self.assertEqual(log.name, "test.logger")
        self.assertEqual(log.level, logging.NOTSET)

        # Ensure it is a logging.Logger
        self.assertIsInstance(log, logging.Logger)
        # Make sure it is stored in all the places we expect
        self.assertEqual(self.manager.getLogger("test.logger"), log)
        self.assertEqual(self.manager.loggerDict["test.logger"], log)

        # Fetch the same logger
        same_log = get_logger("test.logger")
        # Assert we got the same logger
        self.assertEqual(log, same_log)

        # Fetch a different logger
        new_log = get_logger("new.test.logger")
        # Make sure we didn't get the same one
        self.assertNotEqual(log, new_log)

        # If a PlaceHolder is in place of the logger
        # We should return the logging.Logger
        self.assertIsInstance(self.manager.loggerDict["new.test"], logging.PlaceHolder)
        log = get_logger("new.test")
        self.assertEqual(log.name, "new.test")
        self.assertIsInstance(log, logging.Logger)

    @mock.patch("logging.Logger.callHandlers")
    def test_logger_handle_no_limit(self, call_handlers):
        """
        Calling `logging.Logger.handle`
            When no rate limit is set
                Always calls the base `Logger.handle`
        """
        # Configure an INFO logger with no rate limit
        log = get_logger("test.logger")
        log.setLevel(logging.INFO)
        ddtrace.internal.logger._rate_limit = 0

        # Log a bunch of times very quickly (this is fast)
        for _ in range(1000):
            log.info("test")

        # Assert that we did not perform any rate limiting
        self.assertEqual(call_handlers.call_count, 1000)

        # Our buckets are empty
        self.assertEqual(ddtrace.internal.logger._buckets, dict())

    @mock.patch("logging.Logger.callHandlers")
    def test_logger_handle_debug(self, call_handlers):
        """
        Calling `logging.Logger.handle`
            When effective level is DEBUG
                Always calls the base `Logger.handle`
        """
        # Our buckets are empty
        self.assertEqual(ddtrace.internal.logger._buckets, dict())

        # Configure an INFO logger with no rate limit
        log = get_logger("test.logger")
        log.setLevel(logging.DEBUG)
        assert log.getEffectiveLevel() == logging.DEBUG
        assert ddtrace.internal.logger._rate_limit > 0

        # Log a bunch of times very quickly (this is fast)
        for level in ALL_LEVEL_NAMES:
            log_fn = getattr(log, level)
            for _ in range(1000):
                log_fn("test")

        # Assert that we did not perform any rate limiting
        total = 1000 * len(ALL_LEVEL_NAMES)
        self.assertTrue(total <= call_handlers.call_count <= total + 1)

        # Our buckets are empty
        self.assertEqual(ddtrace.internal.logger._buckets, dict())

    @mock.patch("logging.Logger.callHandlers")
    def test_logger_handle_bucket(self, call_handlers):
        """
        When calling `logging.Logger.handle`
            With a record
                We pass it to the base `Logger.handle`
                We create a bucket for tracking
        """
        log = get_logger("test.logger")

        # Create log record and handle it
        record = self._make_record(log)
        first_time = time.monotonic()
        log.handle(record)
        second_time = time.monotonic()

        # We passed to base Logger.handle
        call_handlers.assert_called_once_with(record)

        # We added an bucket entry for this record
        key = (record.name, record.levelno, record.pathname, record.lineno)
        logging_bucket = ddtrace.internal.logger._buckets.get(key)
        self.assertIsInstance(logging_bucket, LoggingBucket)

        # The bucket entry is correct
        assert first_time <= logging_bucket.bucket <= second_time
        self.assertEqual(logging_bucket.skipped, 0)

    @mock.patch("logging.Logger.callHandlers")
    def test_logger_handle_bucket_limited(self, call_handlers):
        """
        When calling `logging.Logger.handle`
            With multiple records in a single time frame
                We pass only the first to the base `Logger.handle`
                We keep track of the number skipped
        """
        log = get_logger("test.logger")

        # Create log record and handle it
        record = self._make_record(log, msg="first")
        first_record = record
        first_time = time.monotonic()
        log.handle(first_record)
        second_time = time.monotonic()

        for _ in range(100):
            record = self._make_record(log)
            # DEV: Use the same timestamp as `first_record` to ensure we are in the same bucket
            record.created = first_record.created
            log.handle(record)

        # We passed to base Logger.handle
        call_handlers.assert_called_once_with(first_record)

        # We added an bucket entry for these records
        key = (record.name, record.levelno, record.pathname, record.lineno)
        logging_bucket = ddtrace.internal.logger._buckets.get(key)
        assert logging_bucket is not None

        # The bucket entry is correct
        assert first_time <= logging_bucket.bucket <= second_time
        self.assertEqual(logging_bucket.skipped, 100)

    @mock.patch("logging.Logger.callHandlers")
    def test_logger_handle_bucket_skipped_msg(self, call_handlers):
        """
        When calling `logging.Logger.handle`
            When a bucket exists for a previous time frame
                We pass only the record to the base `Logger.handle`
                We update the record message to include the number of skipped messages
        """
        log = get_logger("test.logger")

        # Create log record to handle
        original_msg = "hello %s"
        original_args = (1,)
        record = self._make_record(log, msg=original_msg, args=(1,))

        # Create a bucket entry for this record
        key = (record.name, record.levelno, record.pathname, record.lineno)
        bucket = time.monotonic()
        # We want the time bucket to be for an older bucket
        ddtrace.internal.logger._buckets[key] = LoggingBucket(bucket=bucket - 60, skipped=20)

        # Handle our record
        log.handle(record)

        # We passed to base Logger.handle
        call_handlers.assert_called_once_with(record)

        self.assertEqual(record.msg, original_msg + " [20 skipped]")
        self.assertEqual(record.args, original_args)
        self.assertEqual(record.getMessage(), "hello 1 [20 skipped]")

    def test_logger_handle_bucket_key(self):
        """
        When calling `logging.Logger.handle`
            With different log messages
                We use different buckets to limit them
        """
        log = get_logger("test.logger")

        # DEV: This function is inlined in `logger.py`
        def get_key(record):
            return (record.name, record.levelno, record.pathname, record.lineno)

        # Same record signature but different message
        # DEV: These count against the same bucket
        record1 = self._make_record(log, msg="record 1")
        record2 = self._make_record(log, msg="record 2")

        # Different line number (default is `10`)
        record3 = self._make_record(log, lno=10)

        # Different pathnames (default is `module.py`)
        record4 = self._make_record(log, fn="log.py")

        # Different level (default is `logging.INFO`)
        record5 = self._make_record(log, level=logging.WARN)

        # Different logger name
        record6 = self._make_record(log)
        record6.name = "test.logger2"

        # Log all of our records
        all_records = (record1, record2, record3, record4, record5, record6)
        [log.handle(record) for record in all_records]

        buckets = ddtrace.internal.logger._buckets
        # We have 6 records but only end up with 5 buckets
        self.assertEqual(len(buckets), 5)

        # Assert bucket created for the record1 and record2
        bucket1 = buckets[get_key(record1)]
        self.assertEqual(bucket1.skipped, 1)

        bucket2 = buckets[get_key(record2)]
        self.assertEqual(bucket1, bucket2)

        # Assert bucket for the remaining records
        # None of these other messages should have been grouped together
        for record in (record3, record4, record5, record6):
            bucket = buckets[get_key(record)]
            self.assertEqual(bucket.skipped, 0)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE": "true"},
    out=lambda _: "MyThread - ERROR - Hello from thread" in _ and "Dummy" not in _,
)
def test_logger_no_dummy_thread_name_after_module_cleanup():
    import logging
    import sys
    from threading import Thread

    logger = logging.getLogger(__name__)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(threadName)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    t = Thread(target=logger.error, args=("Hello from thread",), name="MyThread")
    t.start()
    t.join()


@pytest.mark.subprocess()
def test_logger_adds_handler_as_default():
    import logging

    import ddtrace  # noqa:F401

    ddtrace_logger = logging.getLogger("ddtrace")

    assert len(ddtrace_logger.handlers) == 1
    assert type(ddtrace_logger.handlers[0]) == logging.StreamHandler


@pytest.mark.subprocess(env=dict(DD_TRACE_LOG_STREAM_HANDLER="false"))
def test_logger_does_not_add_handler_when_configured():
    import logging

    import ddtrace  # noqa:F401

    ddtrace_logger = logging.getLogger("ddtrace")
    assert len(ddtrace_logger.handlers) == 0
    assert ddtrace_logger.handlers == []


def test_logger_log_level_from_env(monkeypatch):
    monkeypatch.setenv("_DD_TESTING_DEBUG_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("_DD_TESTING_WARNING_LOG_LEVEL", "WARNING")

    assert get_logger("ddtrace.testing.debug.foo.bar").level == logging.DEBUG
    assert get_logger("ddtrace.testing.debug.foo").level == logging.DEBUG
    assert get_logger("ddtrace.testing.debug").level == logging.DEBUG
    assert get_logger("ddtrace.testing").level < logging.DEBUG

    assert get_logger("ddtrace.testing.warning.foo.bar").level == logging.WARNING
    assert get_logger("ddtrace.testing.warning.foo").level == logging.WARNING
    assert get_logger("ddtrace.testing.warning").level == logging.WARNING
