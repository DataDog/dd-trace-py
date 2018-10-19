"""
The Grpc integration will trace queries made using the grpc library.

Grpc will be automatically instrumented with ``patch_all``, or when using
the ``ddtrace-run`` command.
Grpc is instrumented on import. To instrument Grpc manually use the
``patch`` function.::

    import grpc
    from ddtrace import patch
    patch(grpc=True)

    # use grpc like usual

To configure the Grpc integration on an per-channel basis use the
``Pin`` API::

    import grpc
    from ddtrace import Pin, patch, Tracer

    patch(grpc=True)
    custom_tracer = Tracer()

    # override the service and tracer to be used
    Pin.override(grpc, service='mygrpc', tracer=custom_tracer)
    with grpc.insecure_channel('localhost:50051' as channel:
        # create stubs and send requests
        pass
"""


from ...utils.importlib import require_modules

required_modules = ["grpc"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ['patch', 'unpatch']
