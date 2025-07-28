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


def patch_iast():
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
    from ddtrace.appsec._iast.taint_sinks.code_injection import patch as code_injection_patch
    from ddtrace.appsec._iast.taint_sinks.command_injection import patch as command_injection_patch
    from ddtrace.appsec._iast.taint_sinks.header_injection import patch as header_injection_patch
    from ddtrace.appsec._iast.taint_sinks.insecure_cookie import patch as insecure_cookie_patch
    from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import patch as unvalidated_redirect_patch
    from ddtrace.appsec._iast.taint_sinks.weak_cipher import patch as weak_cipher_patch
    from ddtrace.appsec._iast.taint_sinks.weak_hash import patch as weak_hash_patch
    from ddtrace.appsec._iast.taint_sinks.xss import patch as xss_patch
    code_injection_patch()
    command_injection_patch()
    header_injection_patch()
    insecure_cookie_patch()
    unvalidated_redirect_patch()
    weak_cipher_patch()
    weak_hash_patch()
    xss_patch()