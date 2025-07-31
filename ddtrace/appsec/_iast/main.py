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
- Code Injection
- SQL Injection
- Cross-Site Scripting (XSS)
- Path Traversal
- Header Injection
- Unvalidated Redirects
- Insecure Cookie
- Server-Side Request Forgery (SSRF)
"""

from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._patch_modules import _apply_custom_security_controls
from ddtrace.appsec._iast._patches.json_tainting import patch as json_tainting_patch
from ddtrace.appsec._iast.secure_marks import cmdi_sanitizer
from ddtrace.appsec._iast.secure_marks import path_traversal_sanitizer
from ddtrace.appsec._iast.secure_marks import sqli_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import header_injection_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import xss_sanitizer
from ddtrace.appsec._iast.secure_marks.validators import header_injection_validator
from ddtrace.appsec._iast.secure_marks.validators import ssrf_validator
from ddtrace.appsec._iast.secure_marks.validators import unvalidated_redirect_validator
from ddtrace.appsec._iast.taint_sinks.code_injection import patch as code_injection_patch
from ddtrace.appsec._iast.taint_sinks.command_injection import patch as command_injection_patch
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as header_injection_patch
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import patch as insecure_cookie_patch
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import patch as unvalidated_redirect_patch
from ddtrace.appsec._iast.taint_sinks.weak_cipher import patch as weak_cipher_patch
from ddtrace.appsec._iast.taint_sinks.weak_hash import patch as weak_hash_patch
from ddtrace.appsec._iast.taint_sinks.xss import patch as xss_patch
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def patch_iast():
    """Patch security-sensitive functions (sink points) for IAST analysis.

    This function implements the core IAST patching mechanism in two phases:

    1. Sink Points Phase (when _DD_IAST_SINK_POINTS_ENABLED):
       - Patches vulnerability detection functions for command injection, XSS,
         code injection, header injection, insecure cookies, and unvalidated redirects

    2. Propagation Phase (when _DD_IAST_PROPAGATION_ENABLED):
       - Enables JSON tainting for data flow tracking
       - Configures sanitizers for input validation (SQL injection, XSS, path traversal)
       - Sets up validators for security checks (SSRF, header injection, unvalidated redirects)
       - Applies custom security controls and taint tracking
    """
    # sink points
    if asm_config._iast_sink_points_enabled:
        code_injection_patch()
        command_injection_patch()
        header_injection_patch()
        insecure_cookie_patch()
        unvalidated_redirect_patch()
        weak_cipher_patch()
        weak_hash_patch()
        xss_patch()

    # propagation
    if asm_config._iast_propagation_enabled:
        json_tainting_patch()

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
        iast_funcs.wrap_function(
            "django.http.response", "ResponseHeaders._convert_to_charset", header_injection_validator
        )

        # Header injection for <= Django 2.2
        iast_funcs.wrap_function(
            "django.http.response", "HttpResponseBase._convert_to_charset", header_injection_validator
        )

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
