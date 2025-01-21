"""
The ``mako`` integration traces templates rendering.
Auto instrumentation is available using ``import ddtrace.auto``. The following is an example::

    import ddtrace.auto

    from mako.template import Template

    t = Template(filename="index.html")

"""


# Required to allow users to import from  `ddtrace.contrib.mako.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001
