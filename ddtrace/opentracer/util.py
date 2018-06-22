
# https://stackoverflow.com/a/26853961
def merge_dicts(x, y):
    """Returns a copy of y merged into x."""
    z = x.copy()   # start with x's keys and values
    z.update(y)    # modifies z with y's keys and values & returns None
    return z


def get_reasonable_service_name():
    """Attempts to find a reasonable service_name in the event that one is not
    provided to the tracer.

    It looks through the current stacktrace looking for a name.
    """
    import inspect
    frm = inspect.stack()[2]
    mod = inspect.getmodule(frm[0])
    name = mod.__name__
    return name
