def called_main(a, b):
    from tests.coverage.sample_code.threading.lib import included_called
    from tests.coverage.not_included_path.not_included import excluded_called

    included_called(a, b)
    excluded_called(a, b)

def called_in_context(a, b):
    from tests.coverage.sample_code.threading.in_context_lib import included_called_in_context
    from tests.coverage.not_included_path.not_included import excluded_called

    included_called_in_context(a, b)
    excluded_called(a, b)

def _never_called():  # Should be covered due to import
    # Should not be covered because it is not called
    pass