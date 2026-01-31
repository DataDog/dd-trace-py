"""
Test that tornado.gen.sleep logs a warning when used.
"""

import logging

from .utils import TornadoTestCase


class TestGenSleepDeprecation(TornadoTestCase):
    """
    Ensure that tornado.gen.sleep logs a warning when used.
    """

    def test_gen_sleep_warning(self):
        from tornado import version_info

        # tornado.gen.sleep was removed in Tornado >=6.3
        if version_info >= (6, 3):
            self.skipTest("tornado.gen.sleep does not exist in Tornado >=6.3")

        import tornado.gen

        with self.assertLogs("ddtrace.contrib.internal.tornado.application", level=logging.WARNING) as log:
            tornado.gen.sleep(0.001)

        self.assertTrue(
            any("ddtrace does not support tornado.gen.sleep" in record.message for record in log.records),
            f"Expected warning not found in logs: {[r.message for r in log.records]}",
        )
