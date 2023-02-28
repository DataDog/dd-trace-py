import os

import psycopg2
from psycopg2.sql import Composable
from psycopg2.sql import SQL

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.psycopg.utils import _patch_extensions
from ddtrace.contrib.psycopg.utils import _unpatch_extensions
from ddtrace.contrib.psycopg.utils import patched_connect
from ddtrace.contrib.psycopg.utils import psycopg_sql_injector_factory
from ddtrace.vendor import wrapt

from ...internal.utils.formats import asbool
from ...internal.utils.version import parse_version
from ...propagation._database_monitoring import _DBM_Propagator


config._add(
    "psycopg2",
    dict(
        _default_service="postgres",
        _dbapi_span_name_prefix="postgres",
        trace_fetch_methods=asbool(os.getenv("DD_PSYCOPG2_TRACE_FETCH_METHODS", default=False)),
        trace_connect=asbool(os.getenv("DD_PSYCOPG2_TRACE_CONNECT", default=False)),
        _dbm_propagator=_DBM_Propagator(
            0, "query", psycopg_sql_injector_factory(composable_class=Composable, sql_class=SQL)
        ),
    ),
)

# Original connect method
_connect = psycopg2.connect

PSYCOPG2_VERSION = parse_version(psycopg2.__version__)


def patch():
    """Patch monkey patches psycopg's connection function
    so that the connection's functions are traced.
    """
    if getattr(psycopg2, "_datadog_patch", False):
        return
    setattr(psycopg2, "_datadog_patch", True)

    config.psycopg2["_extensions_to_patch"] = _psycopg2_extensions
    Pin(_config=config.psycopg2).onto(psycopg2)
    config.psycopg2.base_module = psycopg2

    wrapt.wrap_function_wrapper(psycopg2, "connect", patched_connect)
    _patch_extensions(_psycopg2_extensions)  # do this early just in case


def unpatch():
    if getattr(psycopg2, "_datadog_patch", False):
        setattr(psycopg2, "_datadog_patch", False)
        psycopg2.connect = _connect
        _unpatch_extensions(_psycopg2_extensions)

        pin = Pin.get_from(psycopg2)
        if pin:
            pin.remove_from(psycopg2)


#
# monkeypatch targets
#


def _extensions_register_type(func, _, args, kwargs):
    def _unroll_args(obj, scope=None):
        return obj, scope

    obj, scope = _unroll_args(*args, **kwargs)

    # register_type performs a c-level check of the object
    # type so we must be sure to pass in the actual db connection
    if scope and isinstance(scope, wrapt.ObjectProxy):
        scope = scope.__wrapped__

    return func(obj, scope) if scope else func(obj)


def _extensions_quote_ident(func, _, args, kwargs):
    def _unroll_args(obj, scope=None):
        return obj, scope

    obj, scope = _unroll_args(*args, **kwargs)

    # register_type performs a c-level check of the object
    # type so we must be sure to pass in the actual db connection
    if scope and isinstance(scope, wrapt.ObjectProxy):
        scope = scope.__wrapped__

    return func(obj, scope) if scope else func(obj)


def _extensions_adapt(func, _, args, kwargs):
    adapt = func(*args, **kwargs)
    if hasattr(adapt, "prepare"):
        return AdapterWrapper(adapt)
    return adapt


class AdapterWrapper(wrapt.ObjectProxy):
    def prepare(self, *args, **kwargs):
        func = self.__wrapped__.prepare
        if not args:
            return func(*args, **kwargs)
        conn = args[0]

        # prepare performs a c-level check of the object type so
        # we must be sure to pass in the actual db connection
        if isinstance(conn, wrapt.ObjectProxy):
            conn = conn.__wrapped__

        return func(conn, *args[1:], **kwargs)


# extension hooks
_psycopg2_extensions = [
    (psycopg2.extensions.register_type, psycopg2.extensions, "register_type", _extensions_register_type),
    (psycopg2._psycopg.register_type, psycopg2._psycopg, "register_type", _extensions_register_type),
    (psycopg2.extensions.adapt, psycopg2.extensions, "adapt", _extensions_adapt),
]

# `_json` attribute is only available for psycopg >= 2.5
if getattr(psycopg2, "_json", None):
    _psycopg2_extensions += [
        (psycopg2._json.register_type, psycopg2._json, "register_type", _extensions_register_type),
    ]

# `quote_ident` attribute is only available for psycopg >= 2.7
if getattr(psycopg2, "extensions", None) and getattr(psycopg2.extensions, "quote_ident", None):
    _psycopg2_extensions += [
        (psycopg2.extensions.quote_ident, psycopg2.extensions, "quote_ident", _extensions_quote_ident),
    ]
