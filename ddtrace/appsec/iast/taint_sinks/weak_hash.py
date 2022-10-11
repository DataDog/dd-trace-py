import hashlib
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast.constants import EVIDENCE_ALGORITHM_TYPE
from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec.iast.reporter import report_vulnerability
from ddtrace.appsec.iast.taint_sinks._base import inject_span
from ddtrace.internal.logger import get_logger
from ddtrace.vendor.wrapt import wrap_function_wrapper


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable

    from ddtrace.span import Span

log = get_logger(__name__)

MD5_DEF = "md5"
SHA1_DEF = "md5"


def _wrap_function_wrapper_exception(module, name, wrapper):
    try:
        wrap_function_wrapper(module, name, wrapper)
    except (ImportError, AttributeError):
        log.debug("IAST patching. Module %s.%s not exists", module, name)


def patch():
    # type: () -> None
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    if getattr(hashlib, "_datadog_patch", False):
        return
    setattr(hashlib, "_datadog_patch", True)

    if sys.version_info >= (3, 0, 0):
        _wrap_function_wrapper_exception("_hashlib", "HASH.digest", wrapped_digest_function)
        _wrap_function_wrapper_exception("_hashlib", "HASH.hexdigest", wrapped_digest_function)
        _wrap_function_wrapper_exception(("_%s" % MD5_DEF), "MD5Type.digest", wrapped_md5_function)
        _wrap_function_wrapper_exception(("_%s" % MD5_DEF), "MD5Type.hexdigest", wrapped_md5_function)
        _wrap_function_wrapper_exception(("_%s" % SHA1_DEF), "SHA1Type.digest", wrapped_sha1_function)
        _wrap_function_wrapper_exception(("_%s" % SHA1_DEF), "SHA1Type.hexdigest", wrapped_sha1_function)
    else:
        _wrap_function_wrapper_exception("hashlib", MD5_DEF, wrapped_md5_function)
        _wrap_function_wrapper_exception("hashlib", SHA1_DEF, wrapped_sha1_function)
        _wrap_function_wrapper_exception("hashlib", "new", wrapped_new_function)

    # pycryptodome methods
    _wrap_function_wrapper_exception("Crypto.Hash.MD5", "MD5Hash.digest", wrapped_md5_function)
    _wrap_function_wrapper_exception("Crypto.Hash.MD5", "MD5Hash.hexdigest", wrapped_md5_function)
    _wrap_function_wrapper_exception("Crypto.Hash.SHA1", "SHA1Hash.digest", wrapped_sha1_function)
    _wrap_function_wrapper_exception("Crypto.Hash.SHA1", "SHA1Hash.hexdigest", wrapped_sha1_function)


@inject_span
def wrapped_digest_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    if instance.name.lower() in [MD5_DEF, SHA1_DEF]:
        report_vulnerability(
            span=span,
            vulnerability_type=VULN_INSECURE_HASHING_TYPE,
            evidence_type=EVIDENCE_ALGORITHM_TYPE,
            evidence_value=instance.name,
        )

    return wrapped(*args, **kwargs)


@inject_span
def wrapped_md5_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    return wrapped_function(wrapped, span, MD5_DEF, instance, args, kwargs)


@inject_span
def wrapped_sha1_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    return wrapped_function(wrapped, span, SHA1_DEF, instance, args, kwargs)


def wrapped_function(wrapped, span, evidence, instance, args, kwargs):
    # type: (Callable, Span, str, Any, Any, Any) -> Any
    report_vulnerability(
        span=span,
        vulnerability_type=VULN_INSECURE_HASHING_TYPE,
        evidence_type=EVIDENCE_ALGORITHM_TYPE,
        evidence_value=evidence,
    )

    return wrapped(*args, **kwargs)


@inject_span
def wrapped_new_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    if args[0].lower() in [MD5_DEF, SHA1_DEF]:
        report_vulnerability(
            span=span,
            vulnerability_type=VULN_INSECURE_HASHING_TYPE,
            evidence_type=EVIDENCE_ALGORITHM_TYPE,
            evidence_value=args[0].lower(),
        )

    return wrapped(*args, **kwargs)
