def called_in_session_main(a, b):
    from tests.coverage.excluded_path.excluded import excluded_called
    from tests.coverage.included_path.lib import called_in_session

    called_in_session(a, b)
    excluded_called(a, b)

def called_in_session_import_time():
    from tests.coverage.included_path.lib import uses_constant_in_session
    from tests.coverage.included_path.lib import uses_computed_constant_in_session
    from tests.coverage.included_path.lib import uses_ran_at_import_time_in_session

    uses_constant_in_session()
    uses_computed_constant_in_session()
    uses_ran_at_import_time_in_session()

def called_in_context_main(a, b):
    from tests.coverage.excluded_path.excluded import excluded_called
    from tests.coverage.included_path.in_context_lib import called_in_context

    called_in_context(a, b)
    excluded_called(a, b)

def called_in_context_nested(a, b):
    from tests.coverage.excluded_path.excluded import excluded_called
    from tests.coverage.included_path.in_context_lib import called_in_context

    called_in_context(a, b)
    excluded_called(a, b)

def called_in_context_import_time():
    from tests.coverage.included_path.in_context_lib import uses_constant_in_context
    from tests.coverage.included_path.in_context_lib import uses_computed_constant_in_context
    from tests.coverage.included_path.in_context_lib import uses_ran_at_import_time_in_context

    uses_constant_in_context()
    uses_computed_constant_in_context()
    uses_ran_at_import_time_in_context()

def _never_called():  # Should be covered due to import
    # Should not be covered because it is not called
    pass
