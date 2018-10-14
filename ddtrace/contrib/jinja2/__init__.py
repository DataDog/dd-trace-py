"""
The ``jinja2`` integration traces templates loading, compilation and rendering.
Auto instrumentation is available using the ``patch``. The following is an example::

    from ddtrace import patch
    from jinja2 import Environment, FileSystemLoader

    patch(jinja2=True)

    env = Environment(
        loader=FileSystemLoader("templates")
    )
    template = env.get_template('mytemplate.html')


The library can be configured globally and per instance, using the Configuration API::

    from ddtrace import config

    # By default, service name is inherited from the parent span.
    # We can disable service name inheritance globally
    config.jinja2['inherit_service'] = False

    # change the service name only for this environment
    cfg = config.get_from(env)
    cfg['service_name'] = 'jinja-templates'
"""
from ...utils.importlib import require_modules


required_modules = ['jinja2']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = [
            'patch',
            'unpatch',
        ]
