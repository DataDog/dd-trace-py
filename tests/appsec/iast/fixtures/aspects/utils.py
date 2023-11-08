# import tests.appsec.iast.fixtures.aspects.callees import *
# import tests.appsec.iast.fixtures.aspects.callees as _original_callees


# def bytearray_extend_with_kwargs(*args, dry_run=False):
#     # After visitor would translate as:
#     # bytearray_extend_aspect(original_callees.extend, 1, original_callees, a, b, dry_run=dry_run)
#     return _original_callees.extend(*args, dry_run=dry_run)


# def bytearray_extend_with_kwargs_imported_directly(*args, dry_run=False):
#     # After visitor would translate as:
#     # bytearray_extend_aspect(original_callees.extend, 0, a, b, dry_run=dry_run)
#     return extend(*args, dry_run=dry_run)


# def callee_encode(*args, **kwargs):
#     # After visitor would translate as:
#     # encode_aspect(original_callees.encode, 1, original_callees, a, *args, **kwargs)
#     return _original_callees.encode(*args, **kwargs)


# def callee_direct_encode(*args, **kwargs):
#     # After visitor would translate as:
#     # encode_aspect(original_callees.encode, 0, a, *args, **kwargs)
#     return encode(*args, **kwargs)


# def callee_decode(*args, **kwargs):
#     # After visitor would translate as:
#     # encode_aspect(original_callees.decode, 1, original_callees, a, *args, **kwargs)
#     return _original_callees.decode(*args, **kwargs)


# def callee_direct_decode(*args, **kwargs):
#     # After visitor would translate as:
#     # encode_aspect(original_callees.decode, 0, a, *args, **kwargs)
#     return decode(*args, **kwargs)


# def _function_creator_getattr(function_name):
#     def _new_function(*args, **kwargs):
#         return getattr(_original_callees, function_name)(*args, **kwargs)

#     return _new_function


# for _function_name in [x for x in dir(_original_callees) if not x.startswith(("_", "@"))]:
#     globals()[_function_name] = _function_creator_getattr(_function_name)
#     # _function_creator_imported_direcly(_function_name)


def generate_callers_from_callees(callees_module, callers_file="", callees_module_str=""):
    module_functions = [x for x in dir(callees_module) if not x.startswith(("_", "@"))]

    with open(callers_file, "w") as callers:
        callers.write(f"from {callees_module_str} import *\n")
        callers.write(f"import {callees_module_str} as _original_callees\n")

        for function in module_functions:
            callers.write(
                f"""
def callee_{function}(*args, **kwargs):
    return _original_callees.{function}(*args, **kwargs)

def callee_{function}_direct(*args, **kwargs):
    return {function}(*args, **kwargs)\n
            """
            )
