.. _Configuration:

===============
 Configuration
===============

`ddtrace` can be configured using environment variable. They are listed
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
     - Set an application's environment e.g. ``prod``, ``pre-prod``, ``stage``. Added in ``v0.36.0``.
   * - ``DATADOG_ENV``
     - String
     -
     - Deprecated: use ``DD_ENV``
   * - ``DD_SERVICE``
     - String
     - (autodetected)
     - Set the service name to be used for this application. A default is
       provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`,
       :ref:`pyramid`, :ref:`pylons`, :ref:`tornado`, :ref:`celery`, :ref:`django` and
       :ref:`falcon`. Added in ``v0.36.0``.
   * - ``DD_SERVICE_NAME`` or ``DATADOG_SERVICE_NAME``
     - String
     -
     - Deprecated: use ``DD_SERVICE``.
   * - ``DD_TAGS``
     - String
     -
     - Set global tags to be attached to every span. e.g. ``key1:value1,key2,value2``. Added in ``v0.38.0``.
   * - ``DD_VERSION``
     - String
     -
     - Set an application's version in traces and logs e.g. ``1.2.3``,
       ``6c44da20``, ``2020.02.13``. Added in ``v0.36.0``.
   * - ``DD_SITE``
     - String
     - datadoghq.com
     - Specify which site to use for uploading profiles. Set to
       ``datadoghq.eu`` to use EU site.
   * - ``DATADOG_TRACE_ENABLED``
     - Boolean
     - True
     - Enable web framework and library instrumentation. When false, your
       application code will not generate any traces.
   * - ``DATADOG_TRACE_DEBUG``
     - Boolean
     - False
     - Enable debug logging in the tracer
   * - ``DATADOG_PATCH_MODULES``
     - String
     -
     - Override the modules patched for this execution of the program. Must be
       a list in the ``module1:boolean,module2:boolean`` format. For example,
       ``boto:true,redis:false``.
   * - ``DATADOG_PRIORITY_SAMPLING``
     - Boolean
     - True
     - Enables :ref:`Priority Sampling`.
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
   * - ``DATADOG_TRACE_AGENT_HOSTNAME``
     - String
     -
     - Deprecated: use ``DD_TRACE_AGENT_URL``
   * - ``DATADOG_TRACE_AGENT_PORT``
     - Integer
     -
     - Deprecated: use ``DD_TRACE_AGENT_URL``
   * - ``DD_PROFILING_API_TIMEOUT``
     - Float
     - 10
     - The timeout in seconds before dropping events if the HTTP API does not
       reply.
   * - ``DD_API_KEY``
     - String
     -
     - The Datadog API key to use when uploading profiles.
   * - ``DD_PROFILING_API_URL``
     - URL
     - ``https://intake.profile.datadoghq.com/v1/input``
     - The Datadog API HTTP endpoint to use when uploading events.
   * - ``DD_PROFILING_MAX_TIME_USAGE_PCT``
     - Float
     - 2
     - The percentage of maximum time the stack profiler can use when computing
       statistics. Must be greather than 0 and lesser or equal to 100.
   * - ``DD_PROFILING_MAX_FRAMES``
     - Integer
     - 64
     - The maximum number of frames to capture in stack execution tracing.
   * - ``DD_PROFILING_CAPTURE_PCT``
     - Float
     - 10
     - The percentage of events that should be captured (e.g. memory
       allocation). Greater values reduce the program execution speed. Must be
       greater than 0 lesser or equal to 100.
   * - ``DD_PROFILING_MAX_EVENTS``
     - Integer
     - 49152
     - The maximum number of total events captured that are stored in memory.
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
