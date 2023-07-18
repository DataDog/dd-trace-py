import os
from typing import TYPE_CHECKING

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._patch import set_and_check_module_is_patched
from ddtrace.appsec.iast._patch import set_module_unpatched
from ddtrace.appsec.iast._patch import try_unwrap
from ddtrace.appsec.iast._patch import try_wrap_function_wrapper
from ddtrace.appsec.iast.constants import BLOWFISH_DEF
from ddtrace.appsec.iast.constants import DEFAULT_WEAK_CIPHER_ALGORITHMS
from ddtrace.appsec.iast.constants import DES_DEF
from ddtrace.appsec.iast.constants import EVIDENCE_ALGORITHM_TYPE
from ddtrace.appsec.iast.constants import RC2_DEF
from ddtrace.appsec.iast.constants import RC4_DEF
from ddtrace.appsec.iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Set

log = get_logger(__name__)


def get_weak_cipher_algorithms():
    # type: () -> Set
    CONFIGURED_WEAK_CIPHER_ALGORITHMS = None
    DD_IAST_WEAK_CIPHER_ALGORITHMS = os.getenv("DD_IAST_WEAK_CIPHER_ALGORITHMS")
    if DD_IAST_WEAK_CIPHER_ALGORITHMS:
        CONFIGURED_WEAK_CIPHER_ALGORITHMS = set(
            algo.strip() for algo in DD_IAST_WEAK_CIPHER_ALGORITHMS.lower().split(",")
        )
    return CONFIGURED_WEAK_CIPHER_ALGORITHMS or DEFAULT_WEAK_CIPHER_ALGORITHMS


@oce.register
class WeakCipher(VulnerabilityBase):
    vulnerability_type = VULN_WEAK_CIPHER_TYPE
    evidence_type = EVIDENCE_ALGORITHM_TYPE


def unpatch_iast():
    # type: () -> None
    set_module_unpatched("Crypto", default_attr="_datadog_weak_cipher_patch")
    set_module_unpatched("cryptography", default_attr="_datadog_weak_cipher_patch")

    try_unwrap("Crypto.Cipher.DES", "new")
    try_unwrap("Crypto.Cipher.Blowfish", "new")
    try_unwrap("Crypto.Cipher.ARC2", "new")
    try_unwrap("Crypto.Cipher.ARC4", "ARC4Cipher.encrypt")
    try_unwrap("Crypto.Cipher._mode_cbc", "CbcMode.encrypt")
    try_unwrap("Crypto.Cipher._mode_cfb", "CfbMode.encrypt")
    try_unwrap("Crypto.Cipher._mode_ofb", "OfbMode.encrypt")
    try_unwrap("cryptography.hazmat.primitives.ciphers", "Cipher.encryptor")


def patch():
    # type: () -> None
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    if not set_and_check_module_is_patched("Crypto", default_attr="_datadog_weak_cipher_patch"):
        return
    if not set_and_check_module_is_patched("cryptography", default_attr="_datadog_weak_cipher_patch"):
        return

    weak_cipher_algorithms = get_weak_cipher_algorithms()

    # pycryptodome methods
    if DES_DEF in weak_cipher_algorithms:
        try_wrap_function_wrapper("Crypto.Cipher.DES", "new", wrapped_aux_des_function)
    if BLOWFISH_DEF in weak_cipher_algorithms:
        try_wrap_function_wrapper("Crypto.Cipher.Blowfish", "new", wrapped_aux_blowfish_function)
    if RC2_DEF in weak_cipher_algorithms:
        try_wrap_function_wrapper("Crypto.Cipher.ARC2", "new", wrapped_aux_rc2_function)
    if RC4_DEF in weak_cipher_algorithms:
        try_wrap_function_wrapper("Crypto.Cipher.ARC4", "ARC4Cipher.encrypt", wrapped_rc4_function)

    if weak_cipher_algorithms:
        try_wrap_function_wrapper("Crypto.Cipher._mode_cbc", "CbcMode.encrypt", wrapped_function)
        try_wrap_function_wrapper("Crypto.Cipher._mode_cfb", "CfbMode.encrypt", wrapped_function)
        try_wrap_function_wrapper("Crypto.Cipher._mode_ecb", "EcbMode.encrypt", wrapped_function)
        try_wrap_function_wrapper("Crypto.Cipher._mode_ofb", "OfbMode.encrypt", wrapped_function)

    # cryptography methods
    try_wrap_function_wrapper(
        "cryptography.hazmat.primitives.ciphers", "Cipher.encryptor", wrapped_cryptography_function
    )


def wrapped_aux_rc2_function(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    result._dd_weakcipher_algorithm = "RC2"
    return result


def wrapped_aux_des_function(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    result._dd_weakcipher_algorithm = "DES"
    return result


def wrapped_aux_blowfish_function(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    result._dd_weakcipher_algorithm = "Blowfish"
    return result


@WeakCipher.wrap
def wrapped_rc4_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    WeakCipher.report(
        evidence_value="RC4",
    )
    return wrapped(*args, **kwargs)


@WeakCipher.wrap
def wrapped_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    if hasattr(instance, "_dd_weakcipher_algorithm"):
        evidence = instance._dd_weakcipher_algorithm + "_" + str(instance.__class__.__name__)
        WeakCipher.report(
            evidence_value=evidence,
        )

    return wrapped(*args, **kwargs)


@WeakCipher.wrap
def wrapped_cryptography_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    algorithm_name = instance.algorithm.name.lower()
    if algorithm_name in get_weak_cipher_algorithms():
        WeakCipher.report(
            evidence_value=algorithm_name,
        )
    return wrapped(*args, **kwargs)
