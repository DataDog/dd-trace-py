"""
Instrument ``aiohttp_jinja2`` library to trace aiohttp templates rendering.
This module is optional and you can instrument ``aiohttp`` without instrumenting
the other third party libraries. Actually we're supporting:
* ``aiohttp_jinja2`` for aiohttp templates

``patch_all`` will not instrument this third party module and you must be explicit::

    # TODO: write a better example here
    import aiohttp_jinja2
    from ddtrace import patch

    patch(aiohttp=True)
"""
from ..util import require_modules

required_modules = ['aiohttp_jinja2']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']
