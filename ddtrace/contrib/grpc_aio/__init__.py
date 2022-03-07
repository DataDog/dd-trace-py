"""
The gRPC integration traces the client and server of grpc.aio package using the interceptor pattern.


Enabling
~~~~~~~~

To manually enable the integration::

    from ddtrace import patch
    patch(grpc_aio=True)

    # use grpc like usual
"""


from ...internal.utils.importlib import require_modules


required_modules = ["grpc.aio"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
