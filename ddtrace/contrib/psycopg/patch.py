import os

import psycopg
from psycopg.sql import Composable
from psycopg.sql import SQL

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...internal.utils.formats import asbool
from ...internal.utils.version import parse_version
from ...propagation._database_monitoring import _DBM_Propagator
from ..utils import PsycopgTracedCursor
from ..utils import patched_connect
from ..utils import psycopg_sql_injector_factory


config._add(
    "psycopg",
    dict(
        _default_service="postgres",
        _dbapi_span_name_prefix="postgres",
        trace_fetch_methods=asbool(os.getenv("DD_PSYCOPG_TRACE_FETCH_METHODS", default=False)),
        trace_connect=asbool(os.getenv("DD_PSYCOPG_TRACE_CONNECT", default=False)),
        _dbm_propagator=_DBM_Propagator(
            0, "query", psycopg_sql_injector_factory(composable_class=Composable, sql_class=SQL)
        ),
        dbms_name="postgresql",
    ),
)

# Original connect method
_connect = psycopg.connect

PSYCOPG_VERSION = parse_version(psycopg.__version__)


def patch():
    """Patch monkey patches psycopg's connection function
    so that the connection's functions are traced.
    """
    if getattr(psycopg, "_datadog_patch", False):
        return
    setattr(psycopg, "_datadog_patch", True)

    # config.psycopg._extensions_to_patch = _psycopg_extensions
    Pin(_config=config.psycopg).onto(psycopg)
    config.psycopg.base_module = psycopg

    wrapt.wrap_function_wrapper(psycopg, "connect", patched_connect)
    wrapt.wrap_function_wrapper(psycopg.Connection, "connect", patched_connect)
    wrapt.wrap_function_wrapper(psycopg, "Cursor", PsycopgTracedCursor)
    # _patch_extensions(_psycopg_extensions)  # do this early just in case


def unpatch():
    if getattr(psycopg, "_datadog_patch", False):
        setattr(psycopg, "_datadog_patch", False)
        psycopg.connect = _connect
        #  _unpatch_extensions(_psycopg_extensions)

        pin = Pin.get_from(psycopg)
        if pin:
            pin.remove_from(psycopg)
