from ddtrace.propagation._database_monitoring import default_sql_injector as _default_sql_injector
from ddtrace.vendor import wrapt


def psycopg_sql_injector_factory(composable_class, sql_class):
    def _psycopg_sql_injector(dbm_comment, sql_statement):
        if isinstance(sql_statement, composable_class):
            return sql_class(dbm_comment) + sql_statement
        return _default_sql_injector(dbm_comment, sql_statement)

    return _psycopg_sql_injector


def _patch_extensions(_extensions):
    # we must patch extensions all the time (it's pretty harmless) so split
    # from global patching of connections. must be idempotent.
    for _, module, func, wrapper in _extensions:
        if not hasattr(module, func) or isinstance(getattr(module, func), wrapt.ObjectProxy):
            continue
        wrapt.wrap_function_wrapper(module, func, wrapper)


def _unpatch_extensions(_extensions):
    for original, module, func, _ in _extensions:
        setattr(module, func, original)
