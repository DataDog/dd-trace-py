.. _Configuration:

===============
 Configuration
===============

`ddtrace` can be configured using environment variables. They are listed
below:

.. list-table::
   :widths: 3 1 1 4
   :header-rows: 1

   * - Variable Name
     - Type
     - Default value
     - Description
   * - ``DD_ENV``
     - String
     -
     - Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.
   * - ``DD_SERVICE``
     - String
     - (autodetected)
     - Set the service name to be used for this application. A default is
       provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`,
       :ref:`pyramid`, :ref:`pylons`, :ref:`tornado`, :ref:`celery`, :ref:`django` and
       :ref:`falcon`. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.
   * - ``DD_SERVICE_MAPPING``
     - String
     -
     - Define service name mappings to allow renaming services in traces, e.g. ``postgres:postgresql,defaultdb:postgresql``.
   * - ``DD_TAGS``
     - String
     -
     - Set global tags to be attached to every span. Value must be either comma or space separated. e.g. ``key1:value1,key2,value2`` or ``key1:value key2:value2``. Comma separated support added in ``v0.38.0`` and space separated support added in ``v0.48.0``.
   * - ``DD_VERSION``
     - String
     -
     - Set an application's version in traces and logs e.g. ``1.2.3``,
       ``6c44da20``, ``2020.02.13``. Generally set along with ``DD_SERVICE``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.
   * - ``DD_SITE``
     - String
     - datadoghq.com
     - Specify which site to use for uploading profiles. Set to
       ``datadoghq.eu`` to use EU site.
   * - ``DD_TRACE_ENABLED``
     - Boolean
     - True
     - Enable sending of spans to the Agent. Note that instrumentation will still be installed and spans will be
       generated. Added in ``v0.41.0`` (formerly named ``DATADOG_TRACE_ENABLED``).
   * - ``DD_TRACE_DEBUG``
     - Boolean
     - False
     - Enables debug logging in the tracer. Setting this flag will cause the library to create a root logging handler if
       one does not already exist. Added in ``v0.41.0`` (formerly named ``DATADOG_TRACE_DEBUG``).
   * - ``DD_TRACE_<INTEGRATION>_ENABLED``
     - Boolean
     - True
     - Enables <INTEGRATION> to be patched. For example, ``DD_TRACE_DJANGO_ENABLED=false`` will disable the Django
       integration from being installed. Added in ``v0.41.0``.
   * - ``DATADOG_PATCH_MODULES``
     - String
     -
     - Override the modules patched for this execution of the program. Must be
       a list in the ``module1:boolean,module2:boolean`` format. For example,
       ``boto:true,redis:false``.
   * - ``DD_LOGS_INJECTION``
     - Boolean
     - True
     - Enables :ref:`Logs Injection`.
   * - ``DD_TRACE_AGENT_URL``
     - URL
     - ``http://localhost:8126``
     - The URL to use to connect the Datadog agent. The url can starts with
       ``http://`` to connect using HTTP or with ``unix://`` to use a Unix
       Domain Socket.
   * - ``DD_TRACE_STARTUP_LOGS``
     - Boolean
     - False
     - Enable or disable start up diagnostic logging.
   * - ``DD_TRACE_SAMPLE_RATE``
     - Float
     - 1.0
     - A float, f, 0.0 <= f <= 1.0. f*100% of traces will be sampled.
   * - ``DD_PROFILING_ENABLED``
     - Boolean
     - False
     - Enable Datadog profiling when using ``ddtrace-run``.
   * - ``DD_PROFILING_API_TIMEOUT``
     - Float
     - 10
     - The timeout in seconds before dropping events if the HTTP API does not
       reply.
   * - ``DD_PROFILING_MAX_TIME_USAGE_PCT``
     - Float
     - 1
     - The percentage of maximum time the stack profiler can use when computing
       statistics. Must be greater than 0 and lesser or equal to 100.
   * - ``DD_PROFILING_MAX_FRAMES``
     - Integer
     - 64
     - The maximum number of frames to capture in stack execution tracing.
   * - ``DD_PROFILING_CAPTURE_PCT``
     - Float
     - 2
     - The percentage of events that should be captured (e.g. memory
       allocation). Greater values reduce the program execution speed. Must be
       greater than 0 lesser or equal to 100.
   * - ``DD_PROFILING_UPLOAD_INTERVAL``
     - Float
     - 60
     - The interval in seconds to wait before flushing out recorded events.
   * - ``DD_PROFILING_IGNORE_PROFILER``
     - Boolean
     - True
     - Whether to ignore the profiler in the generated data.
   * - ``DD_PROFILING_TAGS``
     - String
     -
     - The tags to apply to uploaded profile. Must be a list in the
       ``key1:value,key2:value2`` format.

.. _Unified Service Tagging: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/
