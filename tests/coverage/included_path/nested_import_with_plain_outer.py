def call_inner_import():
    def inner():
        from tests.coverage.included_path import nested_import_dependency

        return nested_import_dependency.VALUE

    return inner()
