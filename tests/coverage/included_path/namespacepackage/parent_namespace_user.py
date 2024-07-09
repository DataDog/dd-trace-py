from ..json import JSON_CONST


def function_relative_imported_in_function_using_next_namespace_parent_user():
    str1 = "function imported"
    str2 = "in function"
    return " ".join((str1, str2, JSON_CONST))


relative_module_level_constant = function_relative_imported_in_function_using_next_namespace_parent_user()
