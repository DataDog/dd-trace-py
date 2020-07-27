"""
The PynamoDB integration will trace all db calls made with the pynamodb
library.

This integration is automatically patched when using ``patch_all()``::

    import pynamodb
    from ddtrace import patch, config

    # If not patched yet, you can patch pynamodb specifically
    patch(pynamodb=True)

    # To change the service name
    config.pynamodb['service_name'] = 'differentname'

"""


from ...utils.importlib import require_modules

required_modules = ['pynamodb.connection.base']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        __all__ = ['patch']
