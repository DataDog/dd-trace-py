def called_in_session(a, b):
    return (a, b)


def never_called_in_session():  # Should be covered due to import
    # Should not be covered because it is not
    pass
