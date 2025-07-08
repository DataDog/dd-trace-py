"""Interactive Application Security Testing (IAST) module.

This module implements IAST functionality by patching security-sensitive functions (sink points)
in various Python modules using wrapt. IAST enables runtime security analysis by instrumenting
code to track tainted data propagation through the application.

The patching mechanism works by:
1. Identifying security-sensitive functions (sinks) in various modules
2. Wrapping these functions using wrapt to enable taint tracking
3. Implementing sanitizers and validators for different types of vulnerabilities
4. Enabling propagation tracking through AST-based instrumentation

Supported vulnerability types include:
- Command Injection
- SQL Injection
- Cross-Site Scripting (XSS)
- Path Traversal
- Header Injection
- Unvalidated Redirects
- Weak Cryptography
"""

from wrapt import when_imported

from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._patch_modules import _apply_custom_security_controls
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
    """Patch security-sensitive functions (sink points) for IAST analysis.

    This function implements the core IAST patching mechanism by:
    1. Setting up wrapt-based function wrapping for vulnerability detection
    2. Configuring sanitizers for input validation (e.g., SQL injection, XSS)
    3. Setting up validators for security checks (e.g., SSRF, header injection)
    4. Enabling taint tracking through AST-based propagation

    Args:
        patch_modules (dict): Dictionary of vulnerability types to enable/disable.
            Each key represents a vulnerability type and its boolean value determines
            whether it should be patched. Defaults to IAST_PATCH which enables all
            implemented vulnerability types.

    Note:
        The patching is done using wrapt's when_imported decorator to ensure functions
        are patched when they are first imported. This allows for lazy loading of
        security instrumentation.
    """
    # TODO: Devise the correct patching strategy for IAST
    from ddtrace._monkey import _on_import_factory

    for module in (m for m, e in patch_modules.items() if e):
        when_imported("hashlib")(_on_import_factory(module, "ddtrace.appsec._iast.taint_sinks.%s", raise_errors=False))

    iast_funcs = WrapFunctonsForIAST()

    _apply_custom_security_controls(iast_funcs)

    # CMDI sanitizers
    iast_funcs.wrap_function("shlex", "quote", cmdi_sanitizer)

    # SSRF validators
    iast_funcs.wrap_function("django.utils.http", "url_has_allowed_host_and_scheme", ssrf_validator)

    # SQL sanitizers
    iast_funcs.wrap_function("mysql.connector.conversion", "MySQLConverter.escape", sqli_sanitizer)
    iast_funcs.wrap_function("pymysql.connections", "Connection.escape_string", sqli_sanitizer)
    iast_funcs.wrap_function("pymysql.converters", "escape_string", sqli_sanitizer)

    # Header Injection sanitizers
    iast_funcs.wrap_function("werkzeug.utils", "_str_header_value", header_injection_sanitizer)

    # Header Injection validators
    # Header injection for > Django 3.2
    iast_funcs.wrap_function("django.http.response", "ResponseHeaders._convert_to_charset", header_injection_validator)

    # Header injection for <= Django 2.2
    iast_funcs.wrap_function("django.http.response", "HttpResponseBase._convert_to_charset", header_injection_validator)

    # Unvalidated Redirect validators
    iast_funcs.wrap_function("django.utils.http", "url_has_allowed_host_and_scheme", unvalidated_redirect_validator)

    # Path Traversal sanitizers
    iast_funcs.wrap_function("werkzeug.utils", "secure_filename", path_traversal_sanitizer)

    # TODO: werkzeug.utils.safe_join propagation doesn't work because normpath which is not yet supported by IAST
    #  iast_funcs.wrap_function("werkzeug.utils", "safe_join", path_traversal_sanitizer)
    # TODO: os.path.normpath propagation is not yet supported by IAST
    #  iast_funcs.wrap_function("os.pat", "normpath", path_traversal_sanitizer)

    # XSS sanitizers
    iast_funcs.wrap_function("html", "escape", xss_sanitizer)
    # TODO:  markupsafe._speedups._escape_inner is not yet supported by IAST
    #  iast_funcs.wrap_function("markupsafe", "escape", xss_sanitizer)

    iast_funcs.patch()
    when_imported("json")(_on_import_factory("json_tainting", "ddtrace.appsec._iast._patches.%s", raise_errors=False))
