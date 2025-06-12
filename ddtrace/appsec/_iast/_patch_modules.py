import functools

from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._iast.secure_marks.configuration import SC_SANITIZER
from ddtrace.appsec._iast.secure_marks.configuration import SC_VALIDATOR
from ddtrace.appsec._iast.secure_marks.configuration import SecurityControl
from ddtrace.appsec._iast.secure_marks.configuration import get_security_controls_from_env
from ddtrace.appsec._iast.secure_marks.sanitizers import cmdi_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import create_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import header_injection_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import path_traversal_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import sqli_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import xss_sanitizer
from ddtrace.appsec._iast.secure_marks.validators import create_validator
from ddtrace.appsec._iast.secure_marks.validators import header_injection_validator
from ddtrace.appsec._iast.secure_marks.validators import ssrf_validator
from ddtrace.appsec._iast.secure_marks.validators import unvalidated_redirect_validator
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

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

    # Apply custom security controls from environment variable
    _apply_custom_security_controls()

    # CMDI sanitizers
    when_imported("shlex")(
        lambda _: try_wrap_function_wrapper(
            "shlex",
            "quote",
            cmdi_sanitizer,
        )
    )

    # SSRF
    when_imported("django.utils.http")(
        lambda _: try_wrap_function_wrapper("django.utils.http", "url_has_allowed_host_and_scheme", ssrf_validator)
    )

    # SQL sanitizers
    when_imported("mysql.connector.conversion")(
        lambda _: try_wrap_function_wrapper(
            "mysql.connector.conversion",
            "MySQLConverter.escape",
            sqli_sanitizer,
        )
    )
    when_imported("pymysql")(
        lambda _: try_wrap_function_wrapper("pymysql.connections", "Connection.escape_string", sqli_sanitizer)
    )

    when_imported("pymysql.converters")(
        lambda _: try_wrap_function_wrapper("pymysql.converters", "escape_string", sqli_sanitizer)
    )

    # Header Injection sanitizers
    when_imported("werkzeug.utils")(
        lambda _: try_wrap_function_wrapper(
            "werkzeug.datastructures.headers", "_str_header_value", header_injection_sanitizer
        )
    )

    # Header Injection validators
    # Header injection for > Django 3.2
    when_imported("django.http.response")(
        lambda _: try_wrap_function_wrapper(
            "django.http.response", "ResponseHeaders._convert_to_charset", header_injection_validator
        )
    )
    # Header injection for <= Django 2.2
    when_imported("django.http.response")(
        lambda _: try_wrap_function_wrapper(
            "django.http.response", "HttpResponseBase._convert_to_charset", header_injection_validator
        )
    )

    # Unvalidated Redirect validators
    when_imported("django.utils.http")(
        lambda _: try_wrap_function_wrapper(
            "django.utils.http", "url_has_allowed_host_and_scheme", unvalidated_redirect_validator
        )
    )

    # Path Traversal sanitizers
    when_imported("werkzeug.utils")(
        lambda _: try_wrap_function_wrapper("werkzeug.utils", "secure_filename", path_traversal_sanitizer)
    )
    # TODO: werkzeug.utils.safe_join propagation doesn't work because normpath which is not yet supported by IAST
    # when_imported("werkzeug.utils")(
    #     lambda _: try_wrap_function_wrapper(
    #         "werkzeug.utils",
    #         "safe_join",
    #         path_traversal_sanitizer,
    #     )
    # )
    # TODO: os.path.normpath propagation is not yet supported by IAST
    # when_imported("os.path")(
    #     lambda _: try_wrap_function_wrapper(
    #         "os.path",
    #         "normpath",
    #         path_traversal_sanitizer,
    #     )
    # )

    # XSS sanitizers
    when_imported("html")(
        lambda _: try_wrap_function_wrapper(
            "html",
            "escape",
            xss_sanitizer,
        )
    )
    # TODO:  markupsafe._speedups._escape_inner is not yet supported by IAST
    # when_imported("markupsafe")(
    #     lambda _: try_wrap_function_wrapper(
    #         "html",
    #         "escape",
    #         xss_sanitizer,
    #     )
    # )
    # when_imported("bleach")(
    #     lambda _: try_wrap_function_wrapper(
    #         "bleach",
    #         "clean",
    #         xss_sanitizer,
    #     )
    # )
    when_imported("json")(_on_import_factory("json_tainting", "ddtrace.appsec._iast._patches.%s", raise_errors=False))


def _apply_custom_security_controls():
    """Apply custom security controls from DD_IAST_SECURITY_CONTROLS_CONFIGURATION environment variable."""
    try:
        security_controls = get_security_controls_from_env()

        if not security_controls:
            log.debug("No custom security controls configured")
            return

        log.debug("Applying %s custom security controls", len(security_controls))

        for control in security_controls:
            try:
                _apply_security_control(control)
            except Exception:
                log.warning("Failed to apply security control %s", control, exc_info=True)
    except Exception:
        log.warning("Failed to load custom security controls", exc_info=True)


def _get_module_and_method(security_control: SecurityControl):
    """Determine the target module and method"""
    if "." in security_control.method_name:
        # Handle cases like "Popen.__init__" or "ResponseHeaders._convert_to_charset"
        target_module = security_control.module_path
        target_method = security_control.method_name
    else:
        # Simple method name
        target_module = security_control.module_path
        target_method = security_control.method_name
    return target_module, target_method


def _apply_security_control(control: SecurityControl):
    """Apply a single security control configuration.

    Args:
        control: SecurityControl object containing the configuration
    """
    # Determine the target module and method
    target_module, target_method = _get_module_and_method(control)

    # Create the appropriate wrapper function
    if control.control_type == SC_SANITIZER:
        wrapper_func = functools.partial(create_sanitizer, control.vulnerability_types)
    elif control.control_type == SC_VALIDATOR:
        wrapper_func = functools.partial(create_validator, control.vulnerability_types, control.parameters)
    else:
        log.warning("Unknown control type: %s", control.control_type)

    # Split module path to get the base module for when_imported
    base_module = control.module_path.split(".")[0]
    when_imported(base_module)(
        lambda _: try_wrap_function_wrapper(
            target_module,
            target_method,
            wrapper_func,
        )
    )
    log.debug(
        "Configured %s for %s.%s (vulnerabilities: %s)",
        control.control_type,
        control.module_path,
        control.method_name,
        [v.name for v in control.vulnerability_types],
    )


def _unapply_security_control():
    """Remove security control configuration for testing proposes

    Args:
        control: SecurityControl object containing the configuration
    """
    security_controls = get_security_controls_from_env()
    for control in security_controls:
        target_module, target_method = _get_module_and_method(control)
        try_unwrap(target_module, target_method)
