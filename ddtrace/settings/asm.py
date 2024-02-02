import os.path
from platform import machine
from platform import system

from envier import Env

from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._constants import IAST
from ddtrace.constants import APPSEC_ENV
from ddtrace.constants import IAST_ENV


def _validate_sample_rate(r: float) -> None:
    if r < 0.0 or r > 1.0:
        raise ValueError("sample rate value must be between 0.0 and 1.0")


def build_libddwaf_filename() -> str:
    """
    Build the filename of the libddwaf library to load.
    """
    _DIRNAME = os.path.dirname(os.path.dirname(__file__))
    FILE_EXTENSION = {"Linux": "so", "Darwin": "dylib", "Windows": "dll"}[system()]
    ARCHI = machine().lower()
    # 32-bit-Python on 64-bit-Windows
    if system() == "Windows" and ARCHI == "amd64":
        from sys import maxsize

        if maxsize <= (1 << 32):
            ARCHI = "x86"
    TRANSLATE_ARCH = {"amd64": "x64", "i686": "x86_64", "x86": "win32"}
    ARCHITECTURE = TRANSLATE_ARCH.get(ARCHI, ARCHI)
    return os.path.join(_DIRNAME, "appsec", "_ddwaf", "libddwaf", ARCHITECTURE, "lib", "libddwaf." + FILE_EXTENSION)


class ASMConfig(Env):
    _asm_enabled = Env.var(bool, APPSEC_ENV, default=False)
    _iast_enabled = Env.var(bool, IAST_ENV, default=False)

    _automatic_login_events_mode = Env.var(str, APPSEC.AUTOMATIC_USER_EVENTS_TRACKING, default="safe")
    _user_model_login_field = Env.var(str, APPSEC.USER_MODEL_LOGIN_FIELD, default="")
    _user_model_email_field = Env.var(str, APPSEC.USER_MODEL_EMAIL_FIELD, default="")
    _user_model_name_field = Env.var(str, APPSEC.USER_MODEL_NAME_FIELD, default="")
    _api_security_enabled = Env.var(bool, API_SECURITY.ENV_VAR_ENABLED, default=True)
    _api_security_sample_rate = Env.var(float, API_SECURITY.SAMPLE_RATE, validator=_validate_sample_rate, default=0.1)
    _api_security_parse_response_body = Env.var(bool, API_SECURITY.PARSE_RESPONSE_BODY, default=True)
    _asm_libddwaf = build_libddwaf_filename()
    _asm_libddwaf_available = os.path.exists(_asm_libddwaf)

    _waf_timeout = Env.var(
        float,
        "DD_APPSEC_WAF_TIMEOUT",
        default=DEFAULT.WAF_TIMEOUT,
        help_type=float,
        help="Timeout in microseconds for WAF computations",
    )

    _iast_redaction_enabled = Env.var(bool, "DD_IAST_REDACTION_ENABLED", default=True)
    _iast_redaction_name_pattern = Env.var(
        str,
        "DD_IAST_REDACTION_NAME_PATTERN",
        default=r"(?i)^.*(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|"
        + r"public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|"
        + r"sign(?:ed|ature)?|auth(?:entication|orization)?)",
    )
    _iast_redaction_value_pattern = Env.var(
        str,
        "DD_IAST_REDACTION_VALUE_PATTERN",
        default=r"(?i)bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|"
        + r"ey[I-L][\w=-]+\.ey[I-L][\w=-]+(\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY"
        + r"[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}",
    )
    _iast_lazy_taint = Env.var(bool, IAST.LAZY_TAINT, default=False)

    # for tests purposes
    _asm_config_keys = [
        "_asm_enabled",
        "_iast_enabled",
        "_automatic_login_events_mode",
        "_user_model_login_field",
        "_user_model_email_field",
        "_user_model_name_field",
        "_api_security_enabled",
        "_api_security_sample_rate",
        "_api_security_parse_response_body",
        "_waf_timeout",
        "_iast_redaction_enabled",
        "_iast_redaction_name_pattern",
        "_iast_redaction_value_pattern",
        "_iast_lazy_taint",
        "_asm_config_keys",
    ]


config = ASMConfig()

if not config._asm_libddwaf_available:
    config._asm_enabled = False
    config._iast_enabled = False
