import typing as t

from ddtrace.debugging._expressions import DDCompiler
from ddtrace.debugging._expressions import DDExpression
from ddtrace.internal.settings.dynamic_instrumentation import config
from ddtrace.internal.settings.dynamic_instrumentation import normalize_ident
from ddtrace.internal.utils.cache import cached


# The following identifier represent function argument/local variable/object
# attribute names that should be redacted from the payload.
REDACTED_IDENTIFIERS = (
    frozenset(
        {
            "2fa",
            "accesstoken",
            "aiohttpsession",
            "apikey",
            "apisecret",
            "apisignature",
            "appkey",
            "applicationkey",
            "auth",
            "authorization",
            "authtoken",
            "ccnumber",
            "certificatepin",
            "cipher",
            "clientid",
            "clientsecret",
            "connectionstring",
            "connectsid",
            "cookie",
            "credentials",
            "creditcard",
            "csrf",
            "csrftoken",
            "cvv",
            "databaseurl",
            "dburl",
            "encryptionkey",
            "encryptionkeyid",
            "geolocation",
            "gpgkey",
            "ipaddress",
            "jti",
            "jwt",
            "licensekey",
            "masterkey",
            "mysqlpwd",
            "nonce",
            "oauth",
            "oauthtoken",
            "otp",
            "passhash",
            "passwd",
            "password",
            "passwordb",
            "pemfile",
            "pgpkey",
            "phpsessid",
            "pin",
            "pincode",
            "pkcs8",
            "privatekey",
            "publickey",
            "pwd",
            "recaptchakey",
            "refreshtoken",
            "routingnumber",
            "salt",
            "secret",
            "secretkey",
            "secrettoken",
            "securityanswer",
            "securitycode",
            "securityquestion",
            "serviceaccountcredentials",
            "session",
            "sessionid",
            "sessionkey",
            "setcookie",
            "signature",
            "signaturekey",
            "sshkey",
            "ssn",
            "symfony",
            "token",
            "transactionid",
            "twiliotoken",
            "usersession",
            "voterid",
            "xapikey",
            "xauthtoken",
            "xcsrftoken",
            "xforwardedfor",
            "xrealip",
            "xsrf",
            "xsrftoken",
        }
    )
    | config.redacted_identifiers
)


REDACTED_PLACEHOLDER = r"{redacted}"


@cached()
def redact(ident: t.Union[str, bytes]) -> bool:
    normalized = normalize_ident(ident if isinstance(ident, str) else ident.decode("utf-8", errors="replace"))
    return normalized in REDACTED_IDENTIFIERS and normalized not in config.redaction_excluded_identifiers


@cached()
def redact_type(_type: str) -> bool:
    _re = config.redacted_types_re
    if _re is None:
        return False
    return _re.search(_type) is not None


class DDRedactedExpressionError(Exception):
    pass


class DDRedactedCompiler(DDCompiler):
    @classmethod
    def __getmember__(cls, s: t.Any, a: str) -> t.Any:
        if redact(a):
            raise DDRedactedExpressionError(f"Access to attribute {a!r} is not allowed")

        return super().__getmember__(s, a)

    @classmethod
    def __index__(cls, o: t.Any, i: t.Any) -> t.Any:
        if isinstance(i, (str, bytes)) and redact(i):
            raise DDRedactedExpressionError(f"Access to entry {i!r} is not allowed")

        return super().__index__(o, i)

    @classmethod
    def __ref__(cls, _locals: t.Mapping[str, t.Any], name: str) -> t.Any:
        if redact(name):
            raise DDRedactedExpressionError(f"Access to local {name!r} is not allowed")

        return super().__ref__(_locals, name)


dd_compile_redacted = DDRedactedCompiler().compile


class DDRedactedExpression(DDExpression):
    __compiler__ = dd_compile_redacted
