from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._iast.secure_marks.sanitizers import command_quote_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import secure_filename_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import sql_quote_sanitizer


IAST_PATCH = {
    "code_injection": True,
    "command_injection": True,
    "header_injection": True,
    "insecure_cookie": True,
    "weak_cipher": True,
    "weak_hash": True,
    "xss": True,
}


def patch_iast(patch_modules=IAST_PATCH):
    """Load IAST vulnerabilities sink points.

    IAST_PATCH: list of implemented vulnerabilities
    """
    # TODO: Devise the correct patching strategy for IAST
    from ddtrace._monkey import _on_import_factory

    for module in (m for m, e in patch_modules.items() if e):
        when_imported("hashlib")(_on_import_factory(module, "ddtrace.appsec._iast.taint_sinks.%s", raise_errors=False))

    when_imported("shlex")(
        lambda _: try_wrap_function_wrapper(
            "shlex",
            "quote",
            command_quote_sanitizer,
        )
    )
    when_imported("shlex")(
        lambda _: try_wrap_function_wrapper(
            "shlex",
            "split",
            command_quote_sanitizer,
        )
    )
    when_imported("mysql.connector.conversion")(
        lambda _: try_wrap_function_wrapper(
            "mysql.connector.conversion",
            "MySQLConverter.escape",
            sql_quote_sanitizer,
        )
    )
    when_imported("psycopg2.adapt")(
        lambda _: try_wrap_function_wrapper(
            "psycopg2.adapt",
            "quote_ident",
            sql_quote_sanitizer,
        )
    )
    when_imported("psycopg2.extensions")(
        lambda _: try_wrap_function_wrapper(
            "psycopg2.extensions",
            "quote_ident",
            sql_quote_sanitizer,
        )
    )
    when_imported("werkzeug.utils")(
        lambda _: try_wrap_function_wrapper(
            "werkzeug.utils",
            "secure_filename",
            secure_filename_sanitizer,
        )
    )
    when_imported("json")(_on_import_factory("json_tainting", "ddtrace.appsec._iast._patches.%s", raise_errors=False))
