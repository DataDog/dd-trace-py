def _reraise(tp, value, tb=None):
    """Python 2 re-raise function. This function is internal and
    will be replaced entirely with the `six` library.
    """
    raise tp, value, tb
