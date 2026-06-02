def function_imported_in_function():
    str1 = "function imported"
    str2 = "in function"
    return " ".join((str1, str2))


module_level_constant = function_imported_in_function()
