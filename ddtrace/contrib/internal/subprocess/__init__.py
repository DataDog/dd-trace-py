"""
The subprocess integration will add tracing to all subprocess executions
started in your application. It will be automatically enabled if Application
Security is enabled with::

    DD_APPSEC_ENABLED=true


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.subprocess['sensitive_wildcards']

   Comma separated list of fnmatch-style wildcards Subprocess parameters matching these
   wildcards will be scrubbed and replaced by a "?".

   Default: ``None`` for the config value but note that there are some wildcards always
   enabled in this integration that you can check on
   ``ddtrace.contrib.subprocess.constants.SENSITIVE_WORDS_WILDCARDS``.
"""
