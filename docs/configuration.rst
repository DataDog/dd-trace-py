.. _Configuration:

===============
 Configuration
===============

`ddtrace` can be configured using environment variables. They are listed
below:


.. ddtrace-configuration-options::
   DD_ENV:
     description: |
         Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

   DD_SERVICE:
     default: (autodetected)
     description: |
         Set the service name to be used for this application. A default is
         provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`,
         :ref:`pyramid`, :ref:`pylons`, :ref:`tornado`, :ref:`celery`, :ref:`django` and
         :ref:`falcon`. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.

   DD_SERVICE_MAPPING:
     description: |
         Define service name mappings to allow renaming services in traces, e.g. ``postgres:postgresql,defaultdb:postgresql``.

   DD_TAGS:
     description: |
         Set global tags to be attached to every span. Value must be either comma or space separated. e.g. ``key1:value1,key2,value2`` or ``key1:value key2:value2``.
     version_added:
       v0.38.0: Comma separated support added
       v0.48.0: Space separated support added

   DD_VERSION:
     description: |
         Set an application's version in traces and logs e.g. ``1.2.3``,
         ``6c44da20``, ``2020.02.13``. Generally set along with ``DD_SERVICE``.

         See `Unified Service Tagging`_ for more information.
     version_added:
       v0.36.0:

   DD_SITE:
     default: datadoghq.com
     description: |
         Specify which site to use for uploading profiles. Set to
         ``datadoghq.eu`` to use EU site.

   DD_TRACE_ENABLED:
     type: Boolean
     default: True
     description: |
         Enable sending of spans to the Agent. Note that instrumentation will still be installed and spans will be
         generated.
     version_added:
       v0.41.0: |
           Formerly named ``DATADOG_TRACE_ENABLED``

   DD_INSTRUMENTATION_TELEMETRY_ENABLED:
     type: Boolean
     default: True
     description: |
         Enables sending :ref:`telemetry <Instrumentation Telemetry>` events to the agent.

   DD_TRACE_DEBUG:
     type: Boolean
     default: False
     description: |
         Enables debug logging in the tracer. Setting this flag will cause the library to create a root logging handler if one does not already exist.

         Can be used with `DD_TRACE_LOG_FILE` to route logs to a file.
     version_added:
       v0.41.0: |
           Formerly named ``DATADOG_TRACE_DEBUG``

   DD_TRACE_LOG_FILE_LEVEL:
     default: DEBUG
     description: |
         Configures the ``RotatingFileHandler`` used by the `ddtrace` logger to write logs to a file based on the level specified.
         Defaults to `DEBUG`, but will accept the values found in the standard **logging** library, such as WARNING, ERROR, and INFO,
         if further customization is needed. Files are not written to unless ``DD_TRACE_LOG_FILE`` has been defined.

   DD_TRACE_LOG_FILE:
     description: |
         Directs `ddtrace` logs to a specific file. Note: The default backup count is 1. For larger logs, use with ``DD_TRACE_LOG_FILE_SIZE_BYTES``.
         To fine tune the logging level, use with ``DD_TRACE_LOG_FILE_LEVEL``.

   DD_TRACE_LOG_FILE_SIZE_BYTES:
     type: Int
     default: 15728640
     description: |
         Max size for a file when used with `DD_TRACE_LOG_FILE`. When a log has exceeded this size, there will be one backup log file created.
         In total, the files will store ``2 * DD_TRACE_LOG_FILE_SIZE_BYTES`` worth of logs.

   DD_TRACE_<INTEGRATION>_ENABLED:
     type: Boolean
     default: True
     description: |
         Enables <INTEGRATION> to be patched. For example, ``DD_TRACE_DJANGO_ENABLED=false`` will disable the Django
         integration from being installed.
     version_added:
       v0.41.0:

   DD_PATCH_MODULES:
     description: |
         Override the modules patched for this execution of the program. Must be
         a list in the ``module1:boolean,module2:boolean`` format. For example,
         ``boto:true,redis:false``.
     version_added:
       v0.55.0: |
           Formerly named ``DATADOG_PATCH_MODULES``

   DD_LOGS_INJECTION:
     type: Boolean
     default: False
     description: Enables :ref:`Logs Injection`.

   DD_CALL_BASIC_CONFIG:
     type: Boolean
     default: False
     description: Controls whether ``logging.basicConfig`` is called in ``ddtrace-run`` or when debug mode is enabled.

   DD_TRACE_AGENT_URL:
     type: URL
     default: |
         ``unix:///var/run/datadog/apm.socket`` if available
         otherwise ``http://localhost:8126``
     description: |
           The URL to use to connect the Datadog agent for traces. The url can start with
           ``http://`` to connect using HTTP or with ``unix://`` to use a Unix
           Domain Socket.

           Example for http url: ``DD_TRACE_AGENT_URL=http://localhost:8126``

           Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/apm.socket``

   DD_DOGSTATSD_URL:
     type: URL
     default: |
         ``unix:///var/run/datadog/dsd.socket`` if available
         otherwise ``udp://localhost:8125``
     description: |
         The URL to use to connect the Datadog agent for Dogstatsd metrics. The url can start with
         ``udp://`` to connect using UDP or with ``unix://`` to use a Unix
         Domain Socket.

         Example for UDP url: ``DD_TRACE_AGENT_URL=udp://localhost:8125``

         Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/dsd.socket``

   DD_TRACE_AGENT_TIMEOUT_SECONDS:
     type: Float
     default: 2.0
     description: The timeout in float to use to connect to the Datadog agent.

   DD_TRACE_WRITER_BUFFER_SIZE_BYTES:
     type: Int
     default: 8388608
     description: The max size in bytes of traces to buffer between flushes to the agent.

   DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES:
     type: Int
     default: 8388608
     description: |
         The max size in bytes of each payload item sent to the trace agent. If the max payload size is greater than buffer size,
         then max size of each payload item will be the buffer size.

   DD_TRACE_WRITER_INTERVAL_SECONDS:
     type: Float
     default: 1.0
     description: The time between each flush of traces to the trace agent.

   DD_TRACE_STARTUP_LOGS:
     type: Boolean
     default: False
     description: Enable or disable start up diagnostic logging.

   DD_TRACE_SAMPLE_RATE:
     type: Float
     default: 1.0
     description: A float, f, 0.0 <= f <= 1.0. f*100% of traces will be sampled.

   DD_TRACE_SAMPLING_RULES:
     type: JSON array
     description: |
         A JSON array of objects. Each object must have a “sample_rate”, and the “name” and “service” fields are optional. The “sample_rate” value must be between 0.0 and 1.0 (inclusive).

         **Example:** ``DD_TRACE_SAMPLING_RULES='[{"sample_rate":0.5,"service":"my-service"}]'``

         **Note** that the JSON object must be included in single quotes (') to avoid problems with escaping of the double quote (") character.

   DD_TRACE_HEADER_TAGS:
     description: |
         A map of case-insensitive header keys to tag names. Automatically applies matching header values as tags on root spans.

         For example, ``User-Agent:http.useragent,content-type:http.content_type``.

   DD_TRACE_API_VERSION:
     default: |
         ``v0.4`` if priority sampling is enabled, else ``v0.3``
     description: |
         The trace API version to use when sending traces to the Datadog agent.

         Currently, the supported versions are: ``v0.3``, ``v0.4`` and ``v0.5``.

   DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN:
     default: |
         ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:(?:\s|%20)*(?:=|%3D)[^&]+|(?:"|%22)(?:\s|%20)*(?::|%3A)(?:\s|%20)*(?:"|%22)(?:%2[^2]|%[^2]|[^"%])+(?:"|%22))|bearer(?:\s|%20)+[a-z0-9\._\-]|token(?::|%3A)[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L](?:[\w=-]|%3D)+\.ey[I-L](?:[\w=-]|%3D)+(?:\.(?:[\w.+\/=-]|%3D|%2F|%2B)+)?|[\-]{5}BEGIN(?:[a-z\s]|%20)+PRIVATE(?:\s|%20)KEY[\-]{5}[^\-]+[\-]{5}END(?:[a-z\s]|%20)+PRIVATE(?:\s|%20)KEY|ssh-rsa(?:\s|%20)*(?:[a-z0-9\/\.+]|%2F|%5C|%2B){100,}.``
     description: A regexp to redact sensitive query strings. Obfuscation disabled if set to empty string

   DD_TRACE_PROPAGATION_STYLE_EXTRACT:
     default: |
         ``datadog``
     description: |
         Comma separated list of propagation styles used for extracting trace context from inbound request headers.

         The supported values are ``datadog``, ``b3``, and ``b3 single header``.

         When checking inbound request headers we will take the first valid trace context in the order ``datadog``, ``b3``,
         then ``b3 single header``.

         Example: ``DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3"`` to check for both ``x-datadog-*`` and ``x-b3-*``
         headers when parsing incoming request headers for a trace context.

   DD_TRACE_PROPAGATION_STYLE_INJECT:
     default: |
         ``datadog``
     description: |
         Comma separated list of propagation styles used for injecting trace context into outbound request headers.

         The supported values are ``datadog``, ``b3``, and ``b3 single header``.

         All provided styles are injected into the headers of outbound requests.

         Example: ``DD_TRACE_PROPAGATION_STYLE_INJECT="datadog,b3"`` to inject both ``x-datadog-*`` and ``x-b3-*``
         headers into outbound requests.

   DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH:
     type: Integer
     default: 512
     description: |
         The maximum length of ``x-datadog-tags`` header allowed in the Datadog propagation style.
         Must be a value between 0 to 512. If 0, propagation of ``x-datadog-tags`` is disabled.

   DD_TRACE_PARTIAL_FLUSH_ENABLED:
     type: Boolean
     default: True
     description: Prevents large payloads being sent to APM.

   DD_PROFILING_ENABLED:
     type: Boolean
     default: False
     description: Enable Datadog profiling when using ``ddtrace-run``.

   DD_PROFILING_API_TIMEOUT:
     type: Float
     default: 10
     description: The timeout in seconds before dropping events if the HTTP API does not reply.

   DD_PROFILING_MAX_TIME_USAGE_PCT:
     type: Float
     default: 1
     description: |
         The percentage of maximum time the stack profiler can use when computing
         statistics. Must be greater than 0 and lesser or equal to 100.

   DD_PROFILING_MAX_FRAMES:
     type: Integer
     default: 64
     description: The maximum number of frames to capture in stack execution tracing.

   DD_PROFILING_ENABLE_CODE_PROVENANCE:
     type: Boolean
     default: False
     description: Whether to enable code provenance.

   DD_PROFILING_MEMORY_ENABLED:
     type: Boolean
     default: True
     description: Whether to enable the memory profiler.

   DD_PROFILING_HEAP_ENABLED:
     type: Boolean
     default: True
     description: Whether to enable the heap memory profiler.

   DD_PROFILING_CAPTURE_PCT:
     type: Float
     default: 1
     description: |
         The percentage of events that should be captured (e.g. memory
         allocation). Greater values reduce the program execution speed. Must be
         greater than 0 lesser or equal to 100.

   DD_PROFILING_UPLOAD_INTERVAL:
     type: Float
     default: 60
     description: The interval in seconds to wait before flushing out recorded events.

   DD_PROFILING_IGNORE_PROFILER:
     type: Boolean
     default: False
     description: |
       **Deprecated**: whether to ignore the profiler in the generated data.

   DD_PROFILING_TAGS:
     description: |
         The tags to apply to uploaded profile. Must be a list in the
         ``key1:value,key2:value2`` format.

   DD_PROFILING_ENDPOINT_COLLECTION_ENABLED:
     type: Boolean
     default: True
     description: Whether to enable the endpoint data collection in profiles.

   DD_APPSEC_ENABLED:
     type: Boolean
     default: False
     description: Whether to enable AppSec monitoring.

   DD_APPSEC_RULES:
     type: String
     description: Path to a json file containing AppSec rules.

   DD_COMPILE_DEBUG:
     type: Boolean
     default: False
     description: Compile Cython extensions in RelWithDebInfo mode (with debug info, but no debug code or asserts)

   DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP:
     default: |
       ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization``
     description: Sensitive parameter key regexp for obfuscation.

   DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP:
     default: |
         ``(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}``
     description: Sensitive parameter value regexp for obfuscation.

   DD_HTTP_CLIENT_TAG_QUERY_STRING:
     type: Boolean
     default: True
     description: Send query strings in http.url tag in http client integrations.

   DD_HTTP_SERVER_TAG_QUERY_STRING:
     type: Boolean
     default: True
     description: Send query strings in http.url tag in http server integrations.

   DD_IAST_ENABLED:
     type: Boolean
     default: False
     description: Whether to enable IAST.

   DD_IAST_MAX_CONCURRENT_REQUESTS:
     type: Integer
     default: 2
     description: Number of requests analyzed at the same time.

   DD_IAST_VULNERABILITIES_PER_REQUEST:
     type: Integer
     default: 2
     description: Number of vulnerabilities reported in each request.

.. _Unified Service Tagging: https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/


Dynamic Instrumentation
-----------------------

.. envier:: ddtrace.settings.dynamic_instrumentation:DynamicInstrumentationConfig
