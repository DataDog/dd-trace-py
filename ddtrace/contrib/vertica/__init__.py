"""
The Vertica integration will trace queries made using the vertica-python
library.

Vertica will be automatically instrumented with ``patch_all``, or when using
the ``ddtrace-run`` command.

Vertica is instrumented on import. To instrument Vertica manually use the
``patch`` function. Note the ordering of the following statements::

    from ddtrace import patch
    patch(vertica=True)

    import vertica_python

    # use vertica_python like usual


To configure the Vertica integration you can use the ``Config`` API::

    from ddtrace import config

    config.vertica['service_name'] = 'my-vertica-database'
"""

from ...utils.importlib import require_modules


required_modules = ["vertica_python"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = [patch, unpatch]
