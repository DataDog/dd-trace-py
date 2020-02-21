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
   * - ``DD_SERVICE_NAME`` or ``DATADOG_SERVICE_NAME``
     - String
     - (autodetected)
     - The service name to use.
   * - ``DD_TRACE_AGENT_URL``
     - URL
     - ``http://localhost:8126``
     - The URL to use to connect the Datadog agent. The url can starts with
       ``http://`` to connect using HTTP or with ``unix://`` to use a Unix
       Domain Socket.
   * - ``DD_PROFILING_API_TIMEOUT``
     - Float
     - 10
     - The timeout in seconds before dropping events if the HTTP API does not
       reply.
   * - ``DD_PROFILING_API_KEY``
     - String
     -
     - The Datadog API key to use when uploading events.
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
     - The tags to apply to uploaded profile. Must be a list of in the
       ``key1:value,key2:value2`` format.
