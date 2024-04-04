from ddtrace.vendor.wrapt.importer import when_imported


IAST_PATCH_MODULES = {
    "os": ("command_injection",),
    "subprocess": ("command_injection",),
    "hashlib": ("weak_cipher", "weak_hash"),
}


def patch_iast(patch_modules=IAST_PATCH_MODULES):
    """Load IAST vulnerabilities sink points.

    IAST_PATCH: list of implemented vulnerabilities
    """
    # TODO: Devise the correct patching strategy for IAST
    from ddtrace._monkey import _on_import_factory

    for python_module, vuln_modules in patch_modules.items():
        for vuln_module in vuln_modules:
            when_imported(python_module)(
                _on_import_factory(vuln_module, prefix="ddtrace.appsec._iast.taint_sinks", raise_errors=False)
            )

    when_imported("json")(
        _on_import_factory("json_tainting", prefix="ddtrace.appsec._iast._patches", raise_errors=False)
    )
