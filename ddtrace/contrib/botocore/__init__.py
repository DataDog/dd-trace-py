from ..util import require_modules

required_modules = ['botocore.client']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .client import get_traced_botocore_client  # noqa
        __all__ = ['get_traced_botocore_client']
