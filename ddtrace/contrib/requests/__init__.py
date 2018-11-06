"""
The ``requests`` integration traces all HTTP calls to internal or external services.
Auto instrumentation is available using the ``patch`` function that **must be called
before** importing the ``requests`` library. The following is an example::

    from ddtrace import patch
    patch(requests=True)

    import requests
    requests.get("https://www.datadoghq.com")

If you would prefer finer grained control, use a ``TracedSession`` object as you would a
``requests.Session``::

    from ddtrace.contrib.requests import TracedSession

    session = TracedSession()
    session.get("https://www.datadoghq.com")

The library can be configured globally and per instance, using the Configuration API::

    from ddtrace import config

    # enable distributed tracing globally
    config.requests['distributed_tracing'] = True

    # change the service name only for this session
    session = Session()
    cfg = config.get_from(session)
    cfg['service_name'] = 'auth-api'
"""

from wrapt import wrap_function_wrapper as _w

from ddtrace import config


from ...pin import Pin
from ...utils.install import (
    install_module_import_hook,
    module_patched,
    uninstall_module_import_hook,
)
from ...utils.formats import asbool, get_env
from ...utils.wrappers import unwrap as _u
from .legacy import _distributed_tracing, _distributed_tracing_setter
from .constants import DEFAULT_SERVICE
from .connection import _wrap_request
from ...ext import AppTypes

# requests default settings
config._add('requests',{
    'service_name': get_env('requests', 'service_name', DEFAULT_SERVICE),
    'distributed_tracing': asbool(get_env('requests', 'distributed_tracing', False)),
    'split_by_domain': asbool(get_env('requests', 'split_by_domain', False)),
})


def _patch(requests):
    """Activate http calls tracing"""
    _w('requests', 'Session.request', _wrap_request)
    Pin(
        service=config.requests['service_name'],
        app='requests',
        app_type=AppTypes.web,
        _config=config.requests,
    ).onto(requests.Session)

    # [Backward compatibility]: `session.distributed_tracing` should point and
    # update the `Pin` configuration instead. This block adds a property so that
    # old implementations work as expected
    fn = property(_distributed_tracing)
    fn = fn.setter(_distributed_tracing_setter)
    requests.Session.distributed_tracing = fn


def patch():
    install_module_import_hook('requests', _patch)


def unpatch():
    """Disable traced sessions"""
    import requests

    if not module_patched(requests):
        return

    _u(requests.Session, 'request')
    uninstall_module_import_hook('requests')


# For backwards compatibility
def TracedSession(*args, **kwargs):
    import requests

    session = requests.Session(*args, **kwargs)
    _w(session, 'request', _wrap_request)
    return session
