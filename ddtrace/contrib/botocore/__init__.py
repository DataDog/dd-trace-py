from ..util import require_modules

required_modules = ['botocore.client']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .botopatch import patch  # noqa
        __all__ = ['patch']
