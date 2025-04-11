"""
The ``mako`` integration traces templates rendering.
Auto instrumentation is available using ``import ddtrace.auto``. The following is an example::

    import ddtrace.auto

    from mako.template import Template

    t = Template(filename="index.html")

"""
