def numpy_f():
    try:
        raise ValueError("<error_numpy_f>")
    except ValueError:
        return "<except_numpy>"
