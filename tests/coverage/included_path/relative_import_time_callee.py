from .json import JSON_CONST


def function_relative_imported_in_function():
    str1 = "function imported"
    str2 = "in function"
    return " ".join((str1, str2, JSON_CONST))


relative_module_level_constant = function_relative_imported_in_function()


def function_relative_next_namespace_imported_in_function():
    from .namespacepackage.json import JSON_CONST

    str1 = "function imported"
    str2 = "in function"
    return " ".join((str1, str2, JSON_CONST))
