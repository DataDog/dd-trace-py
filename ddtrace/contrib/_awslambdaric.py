"""
The awslambdaric integration traces AWS Lambda Runtime Interface Client (RIC) operations.

Enabling
~~~~~~~~

The awslambdaric integration is enabled automatically when using
``ddtrace-run`` or ``import ddtrace.auto``.

Global Configuration
~~~~~~~~~~~~~~~~~~~

.. py:data:: DD_AWSLAMBDARIC_SERVICE

   The service name reported by default for awslambdaric spans.

   This option can also be set with the ``config.awslambdaric['service_name']`` setting.

   Default: ``'awslambdaric'``

Integration Configuration
~~~~~~~~~~~~~~~~~~~~~~~

No additional configuration options are available for this integration.
""" 