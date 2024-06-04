def included_called(a, b):
    return (a, b)

def never_called():  # Should be covered due to import
    # Should not be covered because it is not
    pass