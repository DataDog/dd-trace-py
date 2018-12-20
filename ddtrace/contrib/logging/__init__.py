"""
The patching of standard library logging to inject tracing information

Example::
    import logging
    from ddtrace import tracer
    from ddtrace import patch_all; patch_all(logging=True)

    logging.basicConfig(
        format='%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] - %(message)s' + \
            '- dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s'
    )
    log = logging.getLogger(__name__)
    log.level = logging.INFO


    @tracer.wrap()
    def foo():
        log.info('Hello!')

    foo()
"""

from ...utils.importlib import require_modules


required_modules = ['logging']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ['patch', 'unpatch']
