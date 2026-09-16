from time import perf_counter_ns
import typing as t

from ddtrace.debugging._expressions import DDCompiler
from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._expressions import EvaluationTimeoutError
from ddtrace.debugging._expressions import get_eval_deadline
from ddtrace.debugging._expressions import iterates
from ddtrace.debugging._expressions import set_eval_deadline
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.dynamic_instrumentation import config
from ddtrace.internal.settings.dynamic_instrumentation import normalize_ident
from ddtrace.internal.utils.cache import cached


log = get_logger(__name__)

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
    def __ref__(cls, s: str) -> str:
        if redact(s):
            raise DDRedactedExpressionError(f"Access to local {s!r} is not allowed")

        return s


dd_compile_redacted = DDRedactedCompiler().compile


def _redacted_expr(exc: Exception) -> t.Callable[[t.Any], t.Any]:
    def _(_: t.Any) -> t.Any:
        raise exc

    return _


class DDRedactedExpression(DDExpression):
    __compiler__ = dd_compile_redacted

    @classmethod
    def on_compiler_error(cls, dsl: str, exc: Exception) -> t.Callable[[t.Any], t.Any]:
        if isinstance(exc, DDRedactedExpressionError):
            log.error("Cannot compile expression that references potential PII: %s", dsl, exc_info=True)
            return _redacted_expr(exc)
        return super().on_compiler_error(dsl, exc)


class DDTimedRedactedExpression(DDRedactedExpression):
    """A DDRedactedExpression whose evaluation is bounded by
    di_config.evaluation_timeout_ms -- used for anything that budget is meant
    to cover (probe conditions, log-message templates, metric-probe value
    expressions), as opposed to snapshot capture expressions, which have
    their own, separate cooperative HourGlass-based timing under
    capture_timeout_ms.

    The bound is cooperative: the expression helpers that iterate check the
    deadline as they go (see _expressions._bounded), so the timeout is
    raised synchronously from our own code, never into user code. On timeout
    this always raises a bare EvaluationTimeoutError, never one wrapped in a
    DDExpressionEvaluationError, so callers need a single except clause.
    """

    # Only expressions that iterate can overrun in a way we can stop, so the
    # deadline is not even set up for the rest (most conditions). True unless
    # compile() finds out otherwise.
    iterates: bool = True

    @classmethod
    def compile(cls, expr: t.Mapping[str, t.Any]) -> "DDTimedRedactedExpression":
        compiled = super().compile(expr)
        compiled.iterates = iterates(expr["json"])
        return compiled

    def eval(self, scope: t.Mapping[str, t.Any]) -> t.Any:
        eval_timeout_ms = config.evaluation_timeout_ms
        if not self.iterates or eval_timeout_ms <= 0:
            return super().eval(scope)

        outer = get_eval_deadline()
        deadline = perf_counter_ns() + int(eval_timeout_ms * 1_000_000)
        if outer is not None and outer < deadline:
            # Nested evaluation (e.g. a probe hit from code the outer
            # expression calls into): the outer budget still applies.
            deadline = outer
        set_eval_deadline(deadline)
        try:
            return super().eval(scope)
        except DDExpressionEvaluationError as e:
            if isinstance(e.__cause__, EvaluationTimeoutError):
                raise e.__cause__ from None
            raise
        finally:
            set_eval_deadline(outer)
