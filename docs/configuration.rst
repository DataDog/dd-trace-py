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

       .. _dd-appsec-enabled:
   * - ``DD_APPSEC_ENABLED``
     - Boolean
     - False
     - Whether to enable AppSec monitoring.

       .. _dd-appsec-rules:
   * - ``DD_APPSEC_RULES``
     - String
     -
     - Path to a json file containing AppSec rules.

       .. _dd-compile-debug:
   * - ``DD_COMPILE_DEBUG``
     - Boolean
     - False
     - Compile Cython extensions in RelWithDebInfo mode (with debug info, but no debug code or asserts)

       .. _dd-appsec-obfuscation-parameter-key-regexp:
   * - ``DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP``
     - String
     - ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization``
     - Sensitive parameter key regexp for obfuscation.

       .. _dd-appsec-obfuscation-parameter-value-regexp:
   * - ``DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP``
     - String
     - ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}``
     - Sensitive parameter value regexp for obfuscation.

.. _Unified Service Tagging: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/


Dynamic Instrumentation
-----------------------

.. envier:: ddtrace.settings.dynamic_instrumentation:DynamicInstrumentationConfig
