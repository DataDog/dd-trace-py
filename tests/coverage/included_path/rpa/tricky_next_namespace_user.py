from .next_namespacepackage.late_import_const import JSON_CONST


def tricky_next_namespace_parent_user():
    str1 = "function imported"
    str2 = "in function"
    return " ".join((str1, str2, JSON_CONST))


relative_module_level_constant = tricky_next_namespace_parent_user()
