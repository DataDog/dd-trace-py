import typing as t

from ddtrace.debugging._expressions import DDCompiler
from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._expressions import EvalBudget
from ddtrace.debugging._expressions import EvaluationTimeoutError
from ddtrace.debugging._expressions import iterates
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


class DDTimedRedactedExpression(DDRedactedExpression):
    """A DDRedactedExpression bounded by an EvalBudget (by default the full
    evaluation_timeout_ms).

    The bound is cooperative: the iterating operators check the deadline as
    they go (see _expressions._bounded), so the timeout is raised from our
    own code, never into user code, as a bare EvaluationTimeoutError.
    """

    # Only expressions that iterate can overrun in a way we can stop, so the
    # deadline is not even set up for the rest (most conditions). Set by
    # compile(): an expression built from any other callable can't take the
    # deadline, so it is never bounded.
    iterates: bool = False

    @classmethod
    def compile(cls, expr: t.Mapping[str, t.Any]) -> "DDTimedRedactedExpression":
        compiled = super().compile(expr)
        compiled.iterates = iterates(expr["json"])
        return compiled

    def eval(self, scope: t.Mapping[str, t.Any], budget: t.Optional[EvalBudget] = None) -> t.Any:
        """Evaluate within what is left of budget, consuming the time the
        evaluation takes, or within the full evaluation_timeout_ms if None.
        """
        eval_timeout_ms = config.evaluation_timeout_ms
        if not self.iterates or eval_timeout_ms <= 0:
            return super().eval(scope)

        if budget is None:
            budget = EvalBudget(int(eval_timeout_ms * 1_000_000))

        with budget as deadline_ns:
            try:
                return self.callable(scope, deadline_ns)
            except EvaluationTimeoutError:
                raise
            except Exception as e:
                raise DDExpressionEvaluationError(self.dsl, e) from e
