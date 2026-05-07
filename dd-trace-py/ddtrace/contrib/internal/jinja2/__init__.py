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


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.jinja2["service"]

   The service name reported by default for jinja2 spans.

   This option can also be set with the ``DD_JINJA2_SERVICE`` environment
   variable.

   By default, the service name is set to None, so it is inherited from the parent span.
   If there is no parent span and the service name is not overridden the agent will drop the traces.

   Default: ``None``
"""
