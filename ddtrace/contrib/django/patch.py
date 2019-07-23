import django


def patch():
    """Patch the instrumented methods
    """
    if getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', True)


def unpatch():
    """Unpatch the instrumented methods
    """
    if not getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', False)
