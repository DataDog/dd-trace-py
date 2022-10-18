import hashlib
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.constants import EVIDENCE_ALGORITHM_TYPE
from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase
from ddtrace.appsec.iast.taint_sinks._base import _wrap_function_wrapper_exception
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable

log = get_logger(__name__)

MD5_DEF = "md5"
SHA1_DEF = "sha1"


@oce.register
class WeakHash(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_HASHING_TYPE
    evicence_type = EVIDENCE_ALGORITHM_TYPE


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


@WeakHash.wrap
def wrapped_digest_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    if instance.name.lower() in [MD5_DEF, SHA1_DEF]:
        WeakHash.report(
            evidence_value=instance.name,
        )

    return wrapped(*args, **kwargs)


@WeakHash.wrap
def wrapped_md5_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    return wrapped_function(wrapped, MD5_DEF, instance, args, kwargs)


@WeakHash.wrap
def wrapped_sha1_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    return wrapped_function(wrapped, SHA1_DEF, instance, args, kwargs)


@WeakHash.wrap
def wrapped_new_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    if args[0].lower() in [MD5_DEF, SHA1_DEF]:
        WeakHash.report(
            evidence_value=args[0].lower(),
        )
    return wrapped(*args, **kwargs)


def wrapped_function(wrapped, evidence, instance, args, kwargs):
    # type: (Callable, str, Any, Any, Any) -> Any
    WeakHash.report(
        evidence_value=evidence,
    )
    return wrapped(*args, **kwargs)
