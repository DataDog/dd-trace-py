from ..util import require_modules

required_modules = ['elasticsearch']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .transport import get_traced_transport

        __all__ = ['get_traced_transport']
