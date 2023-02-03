#!/usr/bin/env python3
import os
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool  # noqa


def _is_python_version_supported():
    # IAST supports Python versions 3.6 to 3.10
    return (3, 6, 0) <= sys.version_info < (3, 11, 0)


def _is_iast_enabled():
    if not asbool(os.getenv("DD_IAST_ENABLED", default=False)):
        return False

    if not _is_python_version_supported():
        log = get_logger(__name__)
        log.info("IAST is not compatible with the current Python version")
        return False

    return True
