from tests.appsec.iast.fixtures.aspects.callees import extend
import tests.appsec.iast.fixtures.aspects.callees as original_callees


def bytearray_extend_with_kwargs(a, b, dry_run=False):
    # After visitor would translate as:
    # bytearray_extend_aspect(original_callees.extend, 1, original_callees, a, b, dry_run=dry_run)
    return original_callees.extend(a, b, dry_run=dry_run)


def bytearray_extend_with_kwargs_imported_directly(a, b, dry_run=False):
    # After visitor would translate as:
    # bytearray_extend_aspect(original_callees.extend, 0, a, b, dry_run=dry_run)
    return extend(a, b, dry_run=dry_run)


def bytearray_extend(a, b, **kwargs):
    # After visitor would translate as:
    # bytearray_extend_aspect(bytearray.extend, 1, a, b)
    a.extend(b)
    return a
