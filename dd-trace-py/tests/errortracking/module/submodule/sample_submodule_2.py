def submodule_2():
    value = ""
    try:
        raise ValueError("<error_function_submodule_2>")
    except Exception:
        value += "<except_submodule_2>"
    return value
