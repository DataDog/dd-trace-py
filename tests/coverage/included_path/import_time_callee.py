def called_in_session_import_time():
    from tests.coverage.included_path.import_time_lib import compute_using_nested_constant

    return compute_using_nested_constant()


def calls_function_imported_in_function():
    from tests.coverage.included_path.imported_in_function_lib import module_level_constant

    return module_level_constant


def _never_called():  # Should be covered due to import
    # Should not be covered because it is not called
    pass


# This comment should affect neither executable nor covered lines
