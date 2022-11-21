from typing import TYPE_CHECKING

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._patch import try_wrap_function_wrapper
from ddtrace.appsec.iast.constants import VULN_WEAK_CIPHER_TYPE
from ddtrace.appsec.iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable

log = get_logger(__name__)


@oce.register
class WeakCipher(VulnerabilityBase):
    vulnerability_type = VULN_WEAK_CIPHER_TYPE
    evidence_type = ""


def patch():
    # type: () -> None
    """Wrap hashing functions.
    Weak hashing algorithms are those that have been proven to be of high risk, or even completely broken,
    and thus are not fit for use.
    """
    # pycryptodome methods
    try_wrap_function_wrapper("Crypto.Cipher.DES", "new", wrapped_aux_des_function)
    try_wrap_function_wrapper("Crypto.Cipher.Blowfish", "new", wrapped_aux_blowfish_function)
    try_wrap_function_wrapper("Crypto.Cipher.ARC2", "new", wrapped_aux_rc2_function)
    try_wrap_function_wrapper("Crypto.Cipher.ARC4", "ARC4Cipher.encrypt", wrapped_rc4_function)
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
    evidence = instance._dd_weakcipher_algorithm + "_" + str(instance.__class__.__name__)
    WeakCipher.report(
        evidence_value=evidence,
    )
    return wrapped(*args, **kwargs)


@WeakCipher.wrap
def wrapped_cryptography_function(wrapped, instance, args, kwargs):
    # type: (Callable, Any, Any, Any) -> Any
    if instance.algorithm.name in ["Blowfish", "RC4", "IDEA"]:
        evidence = instance.algorithm.name
        WeakCipher.report(
            evidence_value=evidence,
        )
    return wrapped(*args, **kwargs)
