def called_in_context(a, b):
    return (a, b)


def never_called_in_context():  # Should be covered due to import
    # Should not be covered because it is not
    pass
