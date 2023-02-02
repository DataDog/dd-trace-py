#!/usr/bin/env python3
import os
import sys

from ddtrace.internal.utils.formats import asbool  # noqa


def _is_iast_enabled():
    return asbool(os.getenv("DD_IAST_ENABLED", default=False)) and (
        (3, 6, 0) < sys.version_info < (3, 11, 0)  # IAST supports Python versions 3.6 to 3.10
    )
