import hashlib
import os
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.constants import DEFAULT_WEAK_HASH_ALGORITHMS
from ddtrace.appsec.iast.constants import EVIDENCE_ALGORITHM_TYPE
from ddtrace.appsec.iast.constants import MD5_DEF
from ddtrace.appsec.iast.constants import SHA1_DEF
from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec.iast.patch import _unwrap_exception
from ddtrace.appsec.iast.patch import _wrap_function_wrapper_exception
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.vendor.wrapt import resolve_path


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable

log = get_logger(__name__)


def get_weak_hash_algorithms():
    CONFIGURED_WEAK_HASH_ALGORITHMS = None
    DD_IAST_WEAK_HASH_ALGORITHMS = os.getenv("DD_IAST_WEAK_HASH_ALGORITHMS")
    if DD_IAST_WEAK_HASH_ALGORITHMS:
        CONFIGURED_WEAK_HASH_ALGORITHMS = set(algo.strip() for algo in DD_IAST_WEAK_HASH_ALGORITHMS.lower().split(","))

    return CONFIGURED_WEAK_HASH_ALGORITHMS or DEFAULT_WEAK_HASH_ALGORITHMS


@oce.register
class WeakHash(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_HASHING_TYPE
    evidence_type = EVIDENCE_ALGORITHM_TYPE


def unpatch_iast():
    setattr(hashlib, "_datadog_patch", False)
    if sys.version_info >= (3, 0, 0):
        _unwrap_exception("_hashlib", "HASH.digest")
        _unwrap_exception("_hashlib", "HASH.hexdigest")
        _unwrap_exception(("_%s" % MD5_DEF), "MD5Type.digest")
        _unwrap_exception(("_%s" % MD5_DEF), "MD5Type.hexdigest")
        _unwrap_exception(("_%s" % SHA1_DEF), "SHA1Type.digest")
        _unwrap_exception(("_%s" % SHA1_DEF), "SHA1Type.hexdigest")
    else:
        _unwrap_exception("hashlib", MD5_DEF)
        _unwrap_exception("hashlib", SHA1_DEF)
        _unwrap_exception("hashlib", "new")

    # pycryptodome methods
    _unwrap_exception("Crypto.Hash.MD5", "MD5Hash.digest")
    _unwrap_exception("Crypto.Hash.MD5", "MD5Hash.hexdigest")
    _unwrap_exception("Crypto.Hash.SHA1", "SHA1Hash.digest")
    _unwrap_exception("Crypto.Hash.SHA1", "SHA1Hash.hexdigest")


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
        if MD5_DEF in get_weak_hash_algorithms():
            _wrap_function_wrapper_exception(("_%s" % MD5_DEF), "MD5Type.digest", wrapped_md5_function)
            _wrap_function_wrapper_exception(("_%s" % MD5_DEF), "MD5Type.hexdigest", wrapped_md5_function)
        if SHA1_DEF in get_weak_hash_algorithms():
            _wrap_function_wrapper_exception(("_%s" % SHA1_DEF), "SHA1Type.digest", wrapped_sha1_function)
            _wrap_function_wrapper_exception(("_%s" % SHA1_DEF), "SHA1Type.hexdigest", wrapped_sha1_function)
    else:
        if MD5_DEF in get_weak_hash_algorithms():
            _wrap_function_wrapper_exception("hashlib", MD5_DEF, wrapped_md5_function)
        if SHA1_DEF in get_weak_hash_algorithms():
            _wrap_function_wrapper_exception("hashlib", SHA1_DEF, wrapped_sha1_function)
        _wrap_function_wrapper_exception("hashlib", "new", wrapped_new_function)

    # pycryptodome methods
    if MD5_DEF in get_weak_hash_algorithms():
        _wrap_function_wrapper_exception("Crypto.Hash.MD5", "MD5Hash.digest", wrapped_md5_function)
        _wrap_function_wrapper_exception("Crypto.Hash.MD5", "MD5Hash.hexdigest", wrapped_md5_function)
    if SHA1_DEF in get_weak_hash_algorithms():
        _wrap_function_wrapper_exception("Crypto.Hash.SHA1", "SHA1Hash.digest", wrapped_sha1_function)
        _wrap_function_wrapper_exception("Crypto.Hash.SHA1", "SHA1Hash.hexdigest", wrapped_sha1_function)


@WeakHash.wrap
def wrapped_digest_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    if instance.name.lower() in get_weak_hash_algorithms():
        WeakHash.report(
            evidence_value=instance.name,
        )

    print("wrapped")

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
    if args[0].lower() in get_weak_hash_algorithms():
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
