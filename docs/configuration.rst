.. _Configuration:

===============
 Configuration
===============

`ddtrace` can be configured using environment variables. They are listed
below:

.. envier:: ddtrace.settings.config:Config
   :recursive: true

.. list-table::
   :widths: 3 1 1 4
   :header-rows: 1

   * - Variable Name
     - Type
     - Default value
     - Description

       .. _dd-profiling-enabled:
   * - ``DD_PROFILING_ENABLED``
     - Boolean
     - False
     - Enable Datadog profiling when using ``ddtrace-run``.

       .. _dd-profiling-api-timeout:
   * - ``DD_PROFILING_API_TIMEOUT``
     - Float
     - 10
     - The timeout in seconds before dropping events if the HTTP API does not
       reply.

       .. _dd-profiling-max-time-usage-pct:
   * - ``DD_PROFILING_MAX_TIME_USAGE_PCT``
     - Float
     - 1
     - The percentage of maximum time the stack profiler can use when computing
       statistics. Must be greater than 0 and lesser or equal to 100.

       .. _dd-profiling-max-frames:
   * - ``DD_PROFILING_MAX_FRAMES``
     - Integer
     - 64
     - The maximum number of frames to capture in stack execution tracing.

       .. _dd-profiling-code-provenance:
   * - ``DD_PROFILING_ENABLE_CODE_PROVENANCE``
     - Boolean
     - False
     - Whether to enable code provenance.

       .. _dd-profiling-memory-enabled:
   * - ``DD_PROFILING_MEMORY_ENABLED``
     - Boolean
     - True
     - Whether to enable the memory profiler.

       .. _dd-profiling-heap-enabled:
   * - ``DD_PROFILING_HEAP_ENABLED``
     - Boolean
     - True
     - Whether to enable the heap memory profiler.

       .. _dd-profiling-capture-pct:
   * - ``DD_PROFILING_CAPTURE_PCT``
     - Float
     - 1
     - The percentage of events that should be captured (e.g. memory
       allocation). Greater values reduce the program execution speed. Must be
       greater than 0 lesser or equal to 100.

       .. _dd-profiling-upload-interval:
   * - ``DD_PROFILING_UPLOAD_INTERVAL``
     - Float
     - 60
     - The interval in seconds to wait before flushing out recorded events.

       .. _dd-profiling-ignore-profiler:
   * - ``DD_PROFILING_IGNORE_PROFILER``
     - Boolean
     - False
     - **Deprecated**: whether to ignore the profiler in the generated data.

       .. _dd-profiling-tags:
   * - ``DD_PROFILING_TAGS``
     - String
     -
     - The tags to apply to uploaded profile. Must be a list in the
       ``key1:value,key2:value2`` format.

       .. _dd-profiling-endpoing-collection-enabled:
   * - ``DD_PROFILING_ENDPOINT_COLLECTION_ENABLED``
     - Boolean
     - True
     - Whether to enable the endpoint data collection in profiles.

.. _Unified Service Tagging: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/
