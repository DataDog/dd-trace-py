import wrapt


def get_psycopg2_extensions(psycopg_module):
    _extensions = [
        (
            psycopg_module.extensions.register_type,
            psycopg_module.extensions,
            "register_type",
            _extensions_register_type,
        ),
        (psycopg_module._psycopg.register_type, psycopg_module._psycopg, "register_type", _extensions_register_type),
        (psycopg_module.extensions.adapt, psycopg_module.extensions, "adapt", _extensions_adapt),
    ]

    # `_json` attribute is only available for psycopg >= 2.5
    if getattr(psycopg_module, "_json", None):
        _extensions += [
            (psycopg_module._json.register_type, psycopg_module._json, "register_type", _extensions_register_type),
        ]

    # `quote_ident` attribute is only available for psycopg >= 2.7
    if getattr(psycopg_module, "extensions", None) and getattr(psycopg_module.extensions, "quote_ident", None):
        _extensions += [
            (psycopg_module.extensions.quote_ident, psycopg_module.extensions, "quote_ident", _extensions_quote_ident),
        ]

    return _extensions


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
