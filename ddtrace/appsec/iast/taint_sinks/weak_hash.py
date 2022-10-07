import hashlib
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast.constants import EVIDENCE_ALGORITHM_TYPE
from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec.iast.reporter import report_vulnerability
from ddtrace.appsec.iast.taint_sinks._base import inject_span
from ddtrace.internal.logger import get_logger
from ddtrace.vendor.wrapt import wrap_function_wrapper


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable

    from ddtrace.span import Span

log = get_logger(__name__)


def wrap_function_wrapper_exception(module, name, wrapper):
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
        wrap_function_wrapper_exception("_hashlib", "HASH.digest", wrapped_digest_function)
        wrap_function_wrapper_exception("_hashlib", "HASH.hexdigest", wrapped_digest_function)
        wrap_function_wrapper_exception("_md5", "MD5Type.digest", wrapped_function)
        wrap_function_wrapper_exception("_md5", "MD5Type.hexdigest", wrapped_function)
        wrap_function_wrapper_exception("_sha1", "SHA1Type.digest", wrapped_function)
        wrap_function_wrapper_exception("_sha1", "SHA1Type.hexdigest", wrapped_function)
    else:
        wrap_function_wrapper_exception("hashlib", "md5", wrapped_function)
        wrap_function_wrapper_exception("hashlib", "sha1", wrapped_function)
        wrap_function_wrapper_exception("hashlib", "new", wrapped_new_function)

    # pycryptodome methods
    wrap_function_wrapper_exception("Crypto.Hash.MD5", "MD5Hash.digest", wrapped_function)
    wrap_function_wrapper_exception("Crypto.Hash.MD5", "MD5Hash.hexdigest", wrapped_function)
    wrap_function_wrapper_exception("Crypto.Hash.SHA1", "SHA1Hash.digest", wrapped_function)
    wrap_function_wrapper_exception("Crypto.Hash.SHA1", "SHA1Hash.hexdigest", wrapped_function)


@inject_span
def wrapped_digest_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    """ """
    if instance.name.lower() in ["md5", "sha1"]:
        report_vulnerability(
            span=span, vulnerability_type=VULN_INSECURE_HASHING_TYPE, evidence_type=EVIDENCE_ALGORITHM_TYPE
        )

    return wrapped(*args, **kwargs)


@inject_span
def wrapped_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    """ """
    report_vulnerability(
        span=span, vulnerability_type=VULN_INSECURE_HASHING_TYPE, evidence_type=EVIDENCE_ALGORITHM_TYPE
    )

    return wrapped(*args, **kwargs)


@inject_span
def wrapped_new_function(wrapped, span, instance, args, kwargs):
    # type: (Callable, Span, Any, Any, Any) -> Any
    """ """
    if args[0].lower() in ["md5", "sha1"]:
        report_vulnerability(
            span=span, vulnerability_type=VULN_INSECURE_HASHING_TYPE, evidence_type=EVIDENCE_ALGORITHM_TYPE
        )

    return wrapped(*args, **kwargs)
