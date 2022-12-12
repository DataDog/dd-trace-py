import os
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._patch import set_and_check_module_is_patched
from ddtrace.appsec.iast._patch import set_module_unpatched
from ddtrace.appsec.iast._patch import try_unwrap
from ddtrace.appsec.iast._patch import try_wrap_function_wrapper
from ddtrace.appsec.iast.constants import DEFAULT_WEAK_HASH_ALGORITHMS
from ddtrace.appsec.iast.constants import EVIDENCE_ALGORITHM_TYPE
from ddtrace.appsec.iast.constants import MD5_DEF
from ddtrace.appsec.iast.constants import SHA1_DEF
from ddtrace.appsec.iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Set

log = get_logger(__name__)


def get_weak_hash_algorithms():
    # type: () -> Set
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
    # type: () -> None
    set_module_unpatched("hashlib", default_attr="_datadog_weak_hash_patch")
    set_module_unpatched("Crypto", default_attr="_datadog_weak_hash_patch")

    if sys.version_info >= (3, 0, 0):
        try_unwrap("_hashlib", "HASH.digest")
        try_unwrap("_hashlib", "HASH.hexdigest")
        try_unwrap(("_%s" % MD5_DEF), "MD5Type.digest")
        try_unwrap(("_%s" % MD5_DEF), "MD5Type.hexdigest")
        try_unwrap(("_%s" % SHA1_DEF), "SHA1Type.digest")
        try_unwrap(("_%s" % SHA1_DEF), "SHA1Type.hexdigest")
    else:
        try_unwrap("hashlib", MD5_DEF)
        try_unwrap("hashlib", SHA1_DEF)
        try_unwrap("hashlib", "new")

    # pycryptodome methods
    try_unwrap("Crypto.Hash.MD5", "MD5Hash.digest")
    try_unwrap("Crypto.Hash.MD5", "MD5Hash.hexdigest")
    try_unwrap("Crypto.Hash.SHA1", "SHA1Hash.digest")
    try_unwrap("Crypto.Hash.SHA1", "SHA1Hash.hexdigest")


def patch():
    # type: () -> None
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """

    if not set_and_check_module_is_patched("hashlib", default_attr="_datadog_weak_hash_patch"):
        return

    if not set_and_check_module_is_patched("Crypto", default_attr="_datadog_weak_hash_patch"):
        return

    weak_hash_algorithms = get_weak_hash_algorithms()

    if sys.version_info >= (3, 0, 0):
        try_wrap_function_wrapper("_hashlib", "HASH.digest", wrapped_digest_function)
        try_wrap_function_wrapper("_hashlib", "HASH.hexdigest", wrapped_digest_function)
        if MD5_DEF in weak_hash_algorithms:
            try_wrap_function_wrapper(("_%s" % MD5_DEF), "MD5Type.digest", wrapped_md5_function)
            try_wrap_function_wrapper(("_%s" % MD5_DEF), "MD5Type.hexdigest", wrapped_md5_function)
        if SHA1_DEF in weak_hash_algorithms:
            try_wrap_function_wrapper(("_%s" % SHA1_DEF), "SHA1Type.digest", wrapped_sha1_function)
            try_wrap_function_wrapper(("_%s" % SHA1_DEF), "SHA1Type.hexdigest", wrapped_sha1_function)
    else:
        if MD5_DEF in weak_hash_algorithms:
            try_wrap_function_wrapper("hashlib", MD5_DEF, wrapped_md5_function)
        if SHA1_DEF in weak_hash_algorithms:
            try_wrap_function_wrapper("hashlib", SHA1_DEF, wrapped_sha1_function)
        try_wrap_function_wrapper("hashlib", "new", wrapped_new_function)

    # pycryptodome methods
    if MD5_DEF in weak_hash_algorithms:
        try_wrap_function_wrapper("Crypto.Hash.MD5", "MD5Hash.digest", wrapped_md5_function)
        try_wrap_function_wrapper("Crypto.Hash.MD5", "MD5Hash.hexdigest", wrapped_md5_function)
    if SHA1_DEF in weak_hash_algorithms:
        try_wrap_function_wrapper("Crypto.Hash.SHA1", "SHA1Hash.digest", wrapped_sha1_function)
        try_wrap_function_wrapper("Crypto.Hash.SHA1", "SHA1Hash.hexdigest", wrapped_sha1_function)


@WeakHash.wrap
def wrapped_digest_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    if instance.name.lower() in get_weak_hash_algorithms():
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
