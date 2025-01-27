"""
The Vertica integration will trace queries made using the vertica-python
library.

Vertica will be automatically instrumented with ``import ddtrace.auto``, or when using
the ``ddtrace-run`` command.

Vertica is instrumented on import. To instrument Vertica manually use the
``patch`` function. Note the ordering of the following statements::

    from ddtrace import patch
    patch(vertica=True)

    import vertica_python

    # use vertica_python like usual


To configure the Vertica integration globally you can use the ``Config`` API::

    from ddtrace import config, patch
    patch(vertica=True)

    config.vertica['service_name'] = 'my-vertica-database'


To configure the Vertica integration on an instance-per-instance basis use the
``Pin`` API::

    from ddtrace import patch
    from ddtrace.trace import Pin
    patch(vertica=True)

    import vertica_python

    conn = vertica_python.connect(**YOUR_VERTICA_CONFIG)

    # override the service
    Pin.override(conn, service='myverticaservice')
"""
