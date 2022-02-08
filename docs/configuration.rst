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

       .. _dd-env:
   * - ``DD_ENV``
     - String
     -
     - Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

       .. _dd-service:
   * - ``DD_SERVICE``
     - String
     - (autodetected)
     - Set the service name to be used for this application. A default is
       provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`,
       :ref:`pyramid`, :ref:`pylons`, :ref:`tornado`, :ref:`celery`, :ref:`django` and
       :ref:`falcon`. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

       .. _dd-service-mapping:
   * - ``DD_SERVICE_MAPPING``
     - String
     -
     - Define service name mappings to allow renaming services in traces, e.g. ``postgres:postgresql,defaultdb:postgresql``.

       .. _dd-tags:
   * - ``DD_TAGS``
     - String
     -
     - Set global tags to be attached to every span. Value must be either comma or space separated. e.g. ``key1:value1,key2,value2`` or ``key1:value key2:value2``. Comma separated support added in ``v0.38.0`` and space separated support added in ``v0.48.0``.

       .. _dd-version:
   * - ``DD_VERSION``
     - String
     -
     - Set an application's version in traces and logs e.g. ``1.2.3``,
       ``6c44da20``, ``2020.02.13``. Generally set along with ``DD_SERVICE``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

       .. _dd-site:
   * - ``DD_SITE``
     - String
     - datadoghq.com
     - Specify which site to use for uploading profiles. Set to
       ``datadoghq.eu`` to use EU site.

       .. _dd-trace-enabled:
   * - ``DD_TRACE_ENABLED``
     - Boolean
     - True
     - Enable sending of spans to the Agent. Note that instrumentation will still be installed and spans will be
       generated. Added in ``v0.41.0`` (formerly named ``DATADOG_TRACE_ENABLED``).

       .. _dd-trace-debug:
   * - ``DD_TRACE_DEBUG``
     - Boolean
     - False
     - Enables debug logging in the tracer. Setting this flag will cause the library to create a root logging handler if
       one does not already exist. Added in ``v0.41.0`` (formerly named ``DATADOG_TRACE_DEBUG``).

       .. _dd-trace-integration-enabled:
   * - ``DD_TRACE_<INTEGRATION>_ENABLED``
     - Boolean
     - True
     - Enables <INTEGRATION> to be patched. For example, ``DD_TRACE_DJANGO_ENABLED=false`` will disable the Django
       integration from being installed. Added in ``v0.41.0``.

       .. _dd-patch-modules:
   * - ``DD_PATCH_MODULES``
     - String
     -
     - Override the modules patched for this execution of the program. Must be
       a list in the ``module1:boolean,module2:boolean`` format. For example,
       ``boto:true,redis:false``. Added in ``v0.55.0`` (formerly named ``DATADOG_PATCH_MODULES``).

       .. _dd-logs-injection:
   * - ``DD_LOGS_INJECTION``
     - Boolean
     - False
     - Enables :ref:`Logs Injection`.

       .. _dd-call-basic-config:
   * - ``DD_CALL_BASIC_CONFIG``
     - Boolean
     - True
     - Controls whether ``logging.basicConfig`` is called in ``ddtrace-run`` or when debug mode is enabled.

       .. _dd-trace-agent-url:
   * - ``DD_TRACE_AGENT_URL``
     - URL
     - ``unix:///var/run/datadog/apm.socket`` if available 
       otherwise ``http://localhost:8126``
     - The URL to use to connect the Datadog agent for traces. The url can start with
       ``http://`` to connect using HTTP or with ``unix://`` to use a Unix
       Domain Socket.   
       Example for http url: ``DD_TRACE_AGENT_URL=http://localhost:8126``
       Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/apm.socket``

       .. _dd-dogstatsd-url:
   * - ``DD_DOGSTATSD_URL``
     - URL
     - ``unix:///var/run/datadog/dsd.socket`` if available 
       otherwise ``udp://localhost:8125``
     - The URL to use to connect the Datadog agent for Dogstatsd metrics. The url can start with
       ``udp://`` to connect using UDP or with ``unix://`` to use a Unix
       Domain Socket.   
       Example for UDP url: ``DD_TRACE_AGENT_URL=udp://localhost:8125``
       Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/dsd.socket``

       .. _dd-trace-agent-timeout-seconds:
   * - ``DD_TRACE_AGENT_TIMEOUT_SECONDS``
     - Float
     - 2.0
     - The timeout in float to use to connect to the Datadog agent.

       .. _dd-trace-writer-buffer-size-bytes:
   * - ``DD_TRACE_WRITER_BUFFER_SIZE_BYTES``
     - Int
     - 8388608
     - The max size in bytes of traces to buffer between flushes to the agent.

       .. _dd-trace-writer-max-payload-size-bytes:
   * - ``DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES``
     - Int
     - 8388608
     - The max size in bytes of each payload item sent to the trace agent. If the max payload size is greater than buffer size, then max size of each payload item will be the buffer size.

       .. _dd-trace-writer-interval-seconds:
   * - ``DD_TRACE_WRITER_INTERVAL_SECONDS``
     - Float
     - 1.0
     - The time between each flush of traces to the trace agent.

       .. _dd-trace-startup-logs:
   * - ``DD_TRACE_STARTUP_LOGS``
     - Boolean
     - False
     - Enable or disable start up diagnostic logging.

       .. _dd-trace-sample-rate:
   * - ``DD_TRACE_SAMPLE_RATE``
     - Float
     - 1.0
     - A float, f, 0.0 <= f <= 1.0. f*100% of traces will be sampled.
   
   * - ``DD_TRACE_SAMPLING_RULES``
     - JSON array
     -
     - A JSON array of objects. Each object must have a “sample_rate”, and the “name” and “service” fields are optional. The “sample_rate” value must be between 0.0 and 1.0 (inclusive). 
       **Example:** ``DD_TRACE_SAMPLING_RULES='[{"sample_rate":0.5,"service":"my-service"}]'``
       **Note** that the JSON object must be included in single quotes (') to avoid problems with escaping of the double quote (") character.

       .. _dd-trace-header-tags:
   * - ``DD_TRACE_HEADER_TAGS``
     - String
     -
     - A map of case-insensitive header keys to tag names. Automatically applies matching header values as tags on root spans.
       For example, ``User-Agent:http.useragent,content-type:http.content_type``.

       .. _dd-trace-api-version:
   * - ``DD_TRACE_API_VERSION``
     - String
     - ``v0.4`` if priority sampling is enabled, else ``v0.3``
     - The trace API version to use when sending traces to the Datadog agent.
       Currently, the supported versions are: ``v0.3``, ``v0.4`` and ``v0.5``.

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

       .. _dd-profiling-heap-enabled:
   * - ``DD_PROFILING_HEAP_ENABLED``
     - Boolean
     - True
     - Whether to enable the heap memory profiler.

       .. _dd-profiling-capture-pct:
   * - ``DD_PROFILING_CAPTURE_PCT``
     - Float
     - 2
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
