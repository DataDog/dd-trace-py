"""IAST (interactive application security testing) analyzes code for security vulnerabilities.

To add new vulnerabilities analyzers (Taint sink) we should update `IAST_PATCH` in `ddtrace._monkey.patch_iast` function
`IAST_PATCH['[my_new_vulnerability]'] = True`

Create new file with the same name: `ddtrace/appsec/iast/taint_sinks/[my_new_vulnerability].py`

Then, implement the `patch()` function and its wrappers.

In order to have the better performance, the Overhead control engine (OCE) helps us to control the overhead of our
wrapped functions. We should create a class that inherit from `ddtrace.appsec.iast.taint_sinks._base.VulnerabilityBase`
and register with `ddtrace.appsec.iast.oce`.

@oce.register
class MyVulnerability(VulnerabilityBase):
    vulnerability_type = "MyVulnerability"
    evidence_type = "kind_of_Vulnerability"

Before that, we should decorate our wrappers with `wrap` method and
report the vulnerabilities with `report` method. OCE will manage the number of requests, number of vulnerabilities
to reduce the overhead.

@WeakHash.wrap
def wrapped_function(wrapped, instance, args, kwargs):
    # type: (Callable, str, Any, Any, Any) -> Any
    WeakHash.report(
        evidence_value=evidence,
    )
    return wrapped(*args, **kwargs)
"""  # noqa: RST201, RST213, RST210
from ddtrace.appsec.iast.overhead_control_engine import OverheadControl


oce = OverheadControl()


__all__ = [
    "oce",
]
