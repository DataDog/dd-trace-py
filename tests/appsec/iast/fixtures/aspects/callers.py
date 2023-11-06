import tests.appsec.iast.fixtures.aspects.callees as original_callees


def bytearray_extend_with_kwargs(a, b, dry_run=False):
    return original_callees.extend(a, b, dry_run=dry_run)


def bytearray_extend(a, b, **kwargs):
    a.extend(b)
    return a
