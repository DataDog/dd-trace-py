#!/usr/bin/env python3

import logging
import os
import sys

from ddtrace.ext import SpanTypes
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import tracer


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
stream_handler = logging.StreamHandler(sys.stdout)
logger.addHandler(stream_handler)

from tests.appsec.iast.fixtures.integration.print_str import print_str  # noqa: E402


def main():
    with tracer.trace("main", span_type=SpanTypes.WEB):
        print_str()


if __name__ == "__main__":
    iast_enabled = bool(os.environ.get("DD_IAST_ENABLED", "") == "true")
    logger.info("IAST env var: %s", iast_enabled)
    tracer.configure(iast_enabled=not iast_enabled)
    main()
    if not iast_enabled:
        # Disabled by env var but then enabled with ``tracer.configure``
        assert asm_config._iast_enabled
        assert "ddtrace.appsec._iast.processor" in sys.modules
    else:
        # Enabled by env var but then disabled with ``tracer.configure``
        assert not asm_config._iast_enabled
        # Module was loaded before
        assert "ddtrace.appsec._iast.processor" in sys.modules
        # But processor is not used by the tracer
        for i in tracer._span_processors:
            assert i.__class__.__name__ != "AppSecIastSpanProcessor"
