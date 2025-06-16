from wrapt import when_imported

from ddtrace.appsec._iast._patch_modules import WrapModulesForIAST
from ddtrace.appsec._iast.secure_marks import cmdi_sanitizer
from ddtrace.appsec._iast.secure_marks import path_traversal_sanitizer
from ddtrace.appsec._iast.secure_marks import sqli_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import header_injection_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import xss_sanitizer
from ddtrace.appsec._iast.secure_marks.validators import header_injection_validator
from ddtrace.appsec._iast.secure_marks.validators import ssrf_validator
from ddtrace.appsec._iast.secure_marks.validators import unvalidated_redirect_validator


IAST_PATCH = {
    "code_injection": True,
    "command_injection": True,
    "header_injection": True,
    "insecure_cookie": True,
    "unvalidated_redirect": True,
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

    warp_modules = WrapModulesForIAST()
    # CMDI sanitizers
    warp_modules.add_module("shlex", "quote", cmdi_sanitizer)

    # SSRF validators
    warp_modules.add_module("django.utils.http", "url_has_allowed_host_and_scheme", ssrf_validator)

    # SQL sanitizers
    warp_modules.add_module("mysql.connector.conversion", "MySQLConverter.escape", sqli_sanitizer)
    warp_modules.add_module("pymysql.connections", "Connection.escape_string", sqli_sanitizer)
    warp_modules.add_module("pymysql.converters", "escape_string", sqli_sanitizer)

    # Header Injection sanitizers
    warp_modules.add_module("werkzeug.utils", "_str_header_value", header_injection_sanitizer)

    # Header Injection validators
    # Header injection for > Django 3.2
    warp_modules.add_module("django.http.response", "ResponseHeaders._convert_to_charset", header_injection_validator)

    # Header injection for <= Django 2.2
    warp_modules.add_module("django.http.response", "HttpResponseBase._convert_to_charset", header_injection_validator)

    # Unvalidated Redirect validators
    warp_modules.add_module("django.utils.http", "url_has_allowed_host_and_scheme", unvalidated_redirect_validator)

    # Path Traversal sanitizers
    warp_modules.add_module("werkzeug.utils", "secure_filename", path_traversal_sanitizer)

    # TODO: werkzeug.utils.safe_join propagation doesn't work because normpath which is not yet supported by IAST
    #  warp_modules.add_module("werkzeug.utils", "safe_join", path_traversal_sanitizer)
    # TODO: os.path.normpath propagation is not yet supported by IAST
    #  warp_modules.add_module("os.pat", "normpath", path_traversal_sanitizer)

    # XSS sanitizers
    warp_modules.add_module("html", "escape", xss_sanitizer)
    # TODO:  markupsafe._speedups._escape_inner is not yet supported by IAST
    #  warp_modules.add_module("markupsafe", "escape", xss_sanitizer)

    warp_modules.patch()
    when_imported("json")(_on_import_factory("json_tainting", "ddtrace.appsec._iast._patches.%s", raise_errors=False))
