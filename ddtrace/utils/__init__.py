# https://stackoverflow.com/a/26853961
def merge_dicts(x, y):
    """Returns a copy of y merged into x."""
    z = x.copy()  # start with x's keys and values
    z.update(y)  # modifies z with y's keys and values & returns None
    return z


def get_module_name(module):
    """Returns a module's name or None if one cannot be found.
    Relevant PEP: https://www.python.org/dev/peps/pep-0451/
    """
    if hasattr(module, '__spec__'):
        return module.__spec__.name
    return getattr(module, '__name__', None)
