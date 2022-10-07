"""
IAST (interactive application security testing) analyzes code for security vulnerabilities.

To add new vulnerabilities analyzers (Taint sink) we should update `IAST_PATCH` in `ddtrace._monkey.patch_iast` function
```
IAST_PATCH = {
    "weak_hash": True,
    "[my_new_vulnerability]": True,
}
```
Create new file with the same name: `ddtrace/appsec/iast/taint_sinks/[my_new_vulnerability].py`

Then, implement the `patch()` function and its wrappers.

When we detect a vulnerability we should report it with `ddtrace.appsec.iast.reporter.report_vulnerability` to add
this information to the context and `ddtrace.appsec.iast.processor` will send this information to the backend at
the end of the request
"""
