import bm


class DDTraceLogger(bm.Scenario):
    main_hub: bool
    enabled: bool

    def run(self):
        import logging

        from ddtrace.internal import logger as ddlogger
        from ddtrace.internal.logger import get_logger

        try:
            from ddtrace.contrib.internal.gevent.logger import main_hub_logging
        except ImportError:
            # Versions without main hub logging measure the plain logger.
            main_hub_logging = getattr(ddlogger, "main_hub_logging", None)
        if self.main_hub and main_hub_logging is not None:
            # As once gevent has created the main hub: the ddtrace loggers hand the records logged off the main
            # thread to it, while the records logged on the main thread stay inline.
            main_hub_logging.set_hub(object(), lambda *args: None)

        logger = get_logger("ddtrace.benchmarks.logger")
        logger.propagate = False
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.DEBUG if self.enabled else logging.WARNING)

        def _(loops):
            for _ in range(loops):
                logger.debug("message %s", 1)

        yield _
