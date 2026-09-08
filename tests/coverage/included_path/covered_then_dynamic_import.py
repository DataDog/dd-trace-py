def cover_file_without_import():
    return "covered"


def import_preimported_dependency():
    from tests.coverage.included_path.preimported_dependency import dependency_function

    return dependency_function()
