def submodule_1():
    value = ""
    try:
        raise RuntimeError("<error_function_submodule_1>")
    except Exception:
        value += "<except_submodule_1>"
    return value
