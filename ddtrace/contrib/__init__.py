
def func_name(f):
    """ Return a human readable version of the function's name. """
    if hasattr(f, '__name__'):
        return "%s.%s" % (f.__module__, f.__name__)
    return "%s.%s" % (f.__module__, f.__class__.__name__)

def module_name(instance):
    return instance.__class__.__module__.split('.')[0]
