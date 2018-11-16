"""
To trace the molten web framework, run ``patch``::

    from ddtrace.contrib.molten import patch

    from molten import App, Route

    # run patching for molten components
    patch()

    def hello(name: str, age: int) -> str:
        return f'Hello {age} year old named {name}!'
    app = App(routes=[Route('/hello/{name}/{age}', hello)])


To enable distributed tracing when using autopatching, set the
``DATADOG_MOLTEN_DISTRIBUTED_TRACING`` environment variable to ``True``.
"""
from ...utils.importlib import require_modules

required_modules = ['molten']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ['patch', 'unpatch']
